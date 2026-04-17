from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import httpx
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_URL = "https://brossard.shop.hyundaicanada.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/inventory",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
}

async def roadster_get(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        r = await client.get(f"{BASE_URL}{path}", params=params, headers=HEADERS)
        r.raise_for_status()
        return r.json()

async def roadster_post(path: str, payload: dict) -> dict:
    headers = {**HEADERS, "Content-Type": "application/json"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        r = await client.post(f"{BASE_URL}{path}", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

@app.get("/search_inventory")
async def search_inventory(
    submodel: Optional[str] = None,
    year: Optional[int] = None,
    per_page: int = 50
):
    params = {"deal_type": "cash", "request_vehicles": 1, "per_page": per_page}
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
            "style_id":       v.get("style_db_id") or int(v.get("style_id", 0) or 0),
            "year":           v.get("year"),
            "model":          v.get("model"),
            "trim":           v.get("trim"),
            "msrp":           v.get("msrp"),
            "roadster_price": v.get("roadster_price"),
            "destination_fee":v.get("destination_fee"),
            "exterior_color": (v.get("exterior_color") or {}).get("label"),
            "interior_color": (v.get("interior_color") or {}).get("label"),
            "engine":         v.get("engine"),
            "transmission":   v.get("transmission"),
            "drivetrain":     v.get("drivetrain"),
            "in_stock":       v.get("in_stock"),
            "date_in_stock":  v.get("date_in_stock"),
        })

    return {"total": data.get("total", 0), "vehicles": vehicles}


class PaymentRequest(BaseModel):
    stock_number:  str
    finance_term:  int   = 84
    finance_down:  float = 5000
    lease_term:    int   = 36
    lease_down:    float = 0
    lease_km:      int   = 20
    frequency:     str   = "week"

@app.post("/get_vehicle_payment")
async def get_vehicle_payment(req: PaymentRequest):
    inv = await roadster_get("/api/dealer_new_inventory", {
        "deal_type": "cash", "request_vehicles": 1, "per_page": 100
    })

    vehicle = next((v for v in inv.get("vehicles", []) if v.get("stock_number") == req.stock_number), None)
    if not vehicle:
        raise HTTPException(status_code=404, detail=f"Stock {req.stock_number} introuvable.")

    vin         = vehicle["vin"]
    msrp        = vehicle["msrp"]
    price       = vehicle["roadster_price"]
    dest_fee    = vehicle.get("destination_fee", 0)
    colors_msrp = (vehicle.get("exterior_color") or {}).get("msrp", 0) or 0
    style_id    = vehicle.get("style_db_id") or int(vehicle.get("style_id", 0) or 0)

    mf, residual_pct = await _get_rates(vin, req.lease_term)

    payload = _build_payload(vin, style_id, req.stock_number, msrp, price,
                              dest_fee, colors_msrp, req, mf, residual_pct)
    result = await roadster_post("/api/calc/payment", payload)
    return _format(vehicle, result, req)

async def _get_rates(vin: str, lease_term: int):
    url = (f"{BASE_URL}/express/{vin}?deal_type=lease"
           f"&payment_frequency=week&deal_down=0&deal_miles=20&deal_months={lease_term}")
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        r = await client.get(url, headers=HEADERS)
        html = r.text
    mf  = float(m.group(1)) if (m := re.search(r'"money_factor"\s*:\s*([0-9.]+)', html)) else 0.002079
    res = int(m.group(1))   if (m := re.search(r'"base_residual_percent"\s*:\s*([0-9]+)', html)) else 57
    return mf, res

def _build_payload(vin, style_id, stock, msrp, price, dest, colors, req, mf, res):
    base = msrp - dest - colors
    return {
        "vin": vin, "style_id": style_id, "deal_type": "finance", "debug": False,
        "inputs": {
            "deals": [
                {"deal_type": "cash", "price": price, "rebate": 0, "dealer_cash": 1000, "fees": {}},
                {"deal_type": "finance", "price": price, "interest_rate": 0.0499,
                 "term": req.finance_term, "payment_frequency": req.frequency,
                 "down_payment": req.finance_down, "dealer_cash": 1000,
                 "applied_dealer_cash": 1000, "lender": "HMFHYN", "rebate": 0, "fees": {}},
                {"deal_type": "lease", "price": price, "buy_rate": mf,
                 "term": req.lease_term, "payment_frequency": req.frequency,
                 "base_residual_percent": res, "annual_mileage": req.lease_km * 1000,
                 "down_payment": req.lease_down, "dealer_cash": 1000, "rebate": 0, "fees": {}}
            ],
            "vehicle": {"used": False, "stock_number": stock, "msrp": msrp,
                        "prices": {"base": base, "destination": dest, "colors": colors,
                                   "packages": 0, "options": 0, "total": msrp}},
            "dealer":   {"id": "r41591", "state": "QC"},
            "customer": {"zip": "J4X 1C2", "city": "Brossard", "state": "QC", "county": ""},
            "calc_options": {"tax_exempt": False, "cash_collection_mode": "customer_cash"}
        }
    }

def _format(vehicle, result, req):
    deals   = {d["deal_type"]: d.get("terms", {}) for d in result.get("deals", [])}
    c, f, l = deals.get("cash", {}), deals.get("finance", {}), deals.get("lease", {})
    pk      = "weekly_payment" if req.frequency == "week" else "monthly_paym
