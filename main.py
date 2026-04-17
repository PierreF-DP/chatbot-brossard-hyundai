from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from playwright.async_api import async_playwright
import httpx
import asyncio
import re

app = FastAPI()

# Autoriser Chatbase à appeler cette API depuis n'importe quel domaine
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────
# SESSION CLOUDFLARE
# Playwright ouvre Chromium, charge la page, récupère les cookies CF.
# Les cookies sont réutilisés pour tous les appels httpx suivants.
# ─────────────────────────────────────────────────────────────────────

BASE_URL = "https://brossard.shop.hyundaicanada.com"
_cf_cookies: dict = {}
_cf_lock = asyncio.Lock()


async def get_cf_session():
    """Ouvre Chromium headless, passe Cloudflare, récupère les cookies."""
    global _cf_cookies
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        await page.goto(f"{BASE_URL}/inventory", wait_until="networkidle")
        cookies = await context.cookies()
        _cf_cookies = {c["name"]: c["value"] for c in cookies}
        await browser.close()
    return _cf_cookies


async def get_cookies():
    """Retourne les cookies existants ou en génère de nouveaux."""
    global _cf_cookies
    async with _cf_lock:
        if not _cf_cookies:
            await get_cf_session()
        return _cf_cookies


async def roadster_get(path: str, params: dict) -> dict:
    """GET vers Roadster avec cookies CF. Rafraîchit la session si 403."""
    cookies = await get_cookies()
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}/inventory",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE_URL}{path}", params=params,
            cookies=cookies, headers=headers, timeout=15
        )
        if r.status_code in (403, 503):
            _cf_cookies.clear()
            cookies = await get_cf_session()
            r = await client.get(
                f"{BASE_URL}{path}", params=params,
                cookies=cookies, headers=headers, timeout=15
            )
        r.raise_for_status()
        return r.json()


async def roadster_post(path: str, payload: dict) -> dict:
    """POST vers Roadster avec cookies CF. Rafraîchit la session si 403."""
    cookies = await get_cookies()
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
        "Referer": f"{BASE_URL}/inventory",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}{path}", json=payload,
            cookies=cookies, headers=headers, timeout=15
        )
        if r.status_code in (403, 503):
            _cf_cookies.clear()
            cookies = await get_cf_session()
            r = await client.post(
                f"{BASE_URL}{path}", json=payload,
                cookies=cookies, headers=headers, timeout=15
            )
        r.raise_for_status()
        return r.json()


# ─────────────────────────────────────────────────────────────────────
# ACTION 1 : search_inventory
# GET /search_inventory?submodel=Elantra&year=2026
# ─────────────────────────────────────────────────────────────────────

@app.get("/search_inventory")
async def search_inventory(
    submodel: Optional[str] = None,
    year: Optional[int] = None,
    per_page: int = 50
):
    """Retourne la liste des véhicules neufs disponibles."""
    params = {
        "deal_type": "cash",
        "request_vehicles": 1,
        "per_page": per_page
    }
    filters = []
    if submodel:
        filters.append(f"submodel:{submodel}")
    if year:
        filters.append(f"year:{year}")
    if filters:
        params["f"] = filters

    data = await roadster_get("/api/dealer_new_inventory", params)

    vehicles = []
    for v in data.get("vehicles", []):
        vehicles.append({
            "stock_number":   v.get("stock_number"),
            "vin":            v.get("vin"),
            "style_id":       v.get("style_db_id") or int(v.get("style_id", 0)),
            "year":           v.get("year"),
            "model":          v.get("model"),
            "trim":           v.get("trim"),
            "msrp":           v.get("msrp"),
            "roadster_price": v.get("roadster_price"),
            "destination_fee":v.get("destination_fee"),
            "exterior_color": v.get("exterior_color", {}).get("label"),
            "interior_color": v.get("interior_color", {}).get("label"),
            "engine":         v.get("engine"),
            "transmission":   v.get("transmission"),
            "drivetrain":     v.get("drivetrain"),
            "in_stock":       v.get("in_stock"),
            "date_in_stock":  v.get("date_in_stock"),
        })

    return {
        "total": data.get("total", 0),
        "vehicles": vehicles
    }


# ─────────────────────────────────────────────────────────────────────
# ACTION 2 : get_vehicle_payment
# POST /get_vehicle_payment
# Body : { stock_number, finance_term, finance_down, lease_term, ... }
# ─────────────────────────────────────────────────────────────────────

class PaymentRequest(BaseModel):
    stock_number:  str
    finance_term:  int   = 84
    finance_down:  float = 5000
    lease_term:    int   = 36      # 24, 36 ou 48
    lease_down:    float = 0
    lease_km:      int   = 20      # en milliers (20 = 20 000 km/an)
    frequency:     str   = "week"  # "week" ou "month"


@app.post("/get_vehicle_payment")
async def get_vehicle_payment(req: PaymentRequest):
    """Calcule les paiements comptant / financement / location pour un stock."""

    # 1. Trouver le véhicule par stock_number dans l'inventaire
    inv = await roadster_get("/api/dealer_new_inventory", {
        "deal_type": "cash",
        "request_vehicles": 1,
        "per_page": 100
    })

    vehicle = None
    for v in inv.get("vehicles", []):
        if v.get("stock_number") == req.stock_number:
            vehicle = v
            break

    if not vehicle:
        raise HTTPException(status_code=404,
                            detail=f"Véhicule avec stock {req.stock_number} introuvable.")

    vin         = vehicle["vin"]
    msrp        = vehicle["msrp"]
    price       = vehicle["roadster_price"]
    dest_fee    = vehicle.get("destination_fee", 0)
    colors_msrp = (vehicle.get("exterior_color") or {}).get("msrp", 0) or 0
    style_id    = vehicle.get("style_db_id") or int(vehicle.get("style_id", 0))

    # 2. Récupérer les taux du programme actuel via la fiche express
    express_html = await _get_express_page(vin, req.lease_term)
    money_factor, residual_pct = _extract_rates(express_html)

    # 3. Construire et envoyer le payload de calcul
    payload = _build_payment_payload(
        vin=vin, style_id=style_id,
        stock_number=req.stock_number,
        msrp=msrp, price=price,
        dest_fee=dest_fee, colors_msrp=colors_msrp,
        finance_term=req.finance_term, finance_down=req.finance_down,
        lease_term=req.lease_term, lease_down=req.lease_down,
        lease_km=req.lease_km * 1000,
        frequency=req.frequency,
        money_factor=money_factor,
        residual_percent=residual_pct
    )

    result = await roadster_post("/api/calc/payment", payload)

    # 4. Formater et retourner
    return _format_response(vehicle, result, req)


# ─────────────────────────────────────────────────────────────────────
# FONCTIONS UTILITAIRES
# ─────────────────────────────────────────────────────────────────────

async def _get_express_page(vin: str, lease_term: int) -> str:
    """Charge la fiche véhicule pour extraire les taux du programme actif."""
    cookies = await get_cookies()
    url = (
        f"{BASE_URL}/express/{vin}"
        f"?deal_type=lease&payment_frequency=week"
        f"&deal_down=0&deal_miles=20&deal_months={lease_term}"
    )
    async with httpx.AsyncClient() as client:
        r = await client.get(
            url, cookies=cookies, timeout=15,
            headers={"Referer": f"{BASE_URL}/inventory"}
        )
        return r.text


def _extract_rates(html: str) -> tuple:
    """Extrait money_factor et base_residual_percent du HTML de la fiche."""
    mf_match  = re.search(r'"money_factor"s*:s*([0-9.]+)', html)
    res_match = re.search(r'"base_residual_percent"s*:s*([0-9]+)', html)
    mf       = float(mf_match.group(1))  if mf_match  else 0.002079
    residual = int(res_match.group(1))   if res_match else 57
    return mf, residual


def _build_payment_payload(
    vin, style_id, stock_number, msrp, price,
    dest_fee, colors_msrp, finance_term, finance_down,
    lease_term, lease_down, lease_km, frequency,
    money_factor, residual_percent
) -> dict:
    base_price = msrp - dest_fee - colors_msrp
    return {
        "vin":       vin,
        "style_id":  style_id,
        "deal_type": "finance",
        "debug":     False,
        "inputs": {
            "deals": [
                {
                    "deal_type":   "cash",
                    "price":       price,
                    "rebate":      0,
                    "dealer_cash": 1000,
                    "fees":        {}
                },
                {
                    "deal_type":           "finance",
                    "price":               price,
                    "interest_rate":       0.0499,
                    "term":                finance_term,
                    "payment_frequency":   frequency,
                    "down_payment":        finance_down,
                    "dealer_cash":         1000,
                    "applied_dealer_cash": 1000,
                    "lender":              "HMFHYN",
                    "rebate":              0,
                    "fees":                {}
                },
                {
                    "deal_type":            "lease",
                    "price":                price,
                    "buy_rate":             money_factor,
                    "term":                 lease_term,
                    "payment_frequency":    frequency,
                    "base_residual_percent": residual_percent,
                    "annual_mileage":       lease_km,
                    "down_payment":         lease_down,
                    "dealer_cash":          1000,
                    "rebate":               0,
                    "fees":                 {}
                }
            ],
            "vehicle": {
                "used":         False,
                "stock_number": stock_number,
                "msrp":         msrp,
                "prices": {
                    "base":        base_price,
                    "destination": dest_fee,
                    "colors":      colors_msrp,
                    "packages":    0,
                    "options":     0,
                    "total":       msrp
                }
            },
            "dealer":   {"id": "r41591", "state": "QC"},
            "customer": {"zip": "J4X 1C2", "city": "Brossard",
                         "state": "QC", "county": ""},
            "calc_options": {
                "tax_exempt":          False,
                "cash_collection_mode": "customer_cash"
            }
        }
    }


def _format_response(vehicle: dict, result: dict, req: PaymentRequest) -> dict:
    deals    = {d["deal_type"]: d.get("terms", {}) for d in result.get("deals", [])}
    c        = deals.get("cash",    {})
    f        = deals.get("finance", {})
    l        = deals.get("lease",   {})
    pmt_key  = "weekly_payment" if req.frequency == "week" else "monthly_payment"
    freq_lbl = "semaine" if req.frequency == "week" else "mois"

    return {
        "vehicule": {
            "stock":    vehicle.get("stock_number"),
            "vin":      vehicle.get("vin"),
            "annee":    vehicle.get("year"),
            "modele":   vehicle.get("model"),
            "version":  vehicle.get("trim"),
            "ext":      (vehicle.get("exterior_color") or {}).get("label"),
            "int":      (vehicle.get("interior_color") or {}).get("label"),
        },
        "comptant": {
            "prix_affiche":        c.get("price"),
            "pdsf":                c.get("msrp"),
            "total_taxes_inclus":  c.get("total_purchase_price"),
            "TPS":                 c.get("primary_tax"),
            "TVQ":                 c.get("secondary_tax"),
        },
        "financement": {
            "frequence":           freq_lbl,
            "paiement":            f.get(pmt_key),
            "paiement_mensuel":    f.get("monthly_payment"),
            "paiement_semaine":    f.get("weekly_payment"),
            "taux_annuel_pct":     round((f.get("interest_rate") or 0) * 100, 2),
            "terme_mois":          f.get("term"),
            "mise_de_fonds":       f.get("customer_cash"),
            "total_finance":       f.get("total_financed"),
            "total_interets":      f.get("total_interest"),
            "preteur":             f.get("lender_name"),
            "du_a_signature":      f.get("due_at_signing"),
        },
        "location": {
            "frequence":           freq_lbl,
            "paiement":            l.get(pmt_key),
            "paiement_mensuel":    l.get("monthly_payment"),
            "paiement_semaine":    l.get("weekly_payment"),
            "money_factor":        l.get("money_factor"),
            "taux_annuel_pct":     round((l.get("apr") or 0) * 100, 2),
            "terme_mois":          l.get("term"),
            "km_annuels":          l.get("annual_mileage"),
            "residuel_pct":        l.get("residual_percent"),
            "residuel_valeur":     l.get("residual_value"),
            "du_a_signature":      l.get("due_at_signing"),
            "surplus_km_par_km":   l.get("annual_mileage_overage_precharge_rate"),
        }
    }
