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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"{BASE_URL}/inventory",
        "Origin": BASE_URL,
        "Connection": "keep-alive",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
}


async def roadster_get(path: str, params: dict) -> dict:
        try:
                    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                                    r = await client.get(f"{BASE_URL}{path}", params=params, headers=HEADERS)
                                    if r.status_code == 403:
                                                        raise HTTPException(status_code=503, detail="Le site du concessionnaire bloque les requetes automatiques (Cloudflare). Essayez plus tard.")
                                                    r.raise_for_status()
                                    return r.json()
        except httpx.TimeoutException:
                    raise HTTPException(status_code=504, detail="Delai d'attente depasse pour le site du concessionnaire.")
except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Erreur du site concessionnaire: HTTP {e.response.status_code}")
except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur inattendue: {str(e)}")


async def roadster_post(path: str, payload: dict) -> dict:
        headers = {**HEADERS, "Content-Type": "application/json"}
        try:
                    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                                    r = await client.post(f"{BASE_URL}{path}", json=payload, headers=headers)
                                    if r.status_code == 403:
                                                        raise HTTPException(status_code=503, detail="Le site du concessionnaire bloque les requetes automatiques (Cloudflare).")
                                                    r.raise_for_status()
                                    return r.json()
        except httpx.TimeoutException:
                    raise HTTPException(status_code=504, detail="Delai d'attente depasse.")
except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Erreur HTTP {e.response.status_code}")
except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")


@app.get("/search_inventory")
async def search_inventory(
        submodel: Optional[str] = None,
        year: Optional[int] = None,
        per_page: int = 50,
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

    inventory = data.get("inventory", [])
    results = []
    for v in inventory:
                results.append({
                                "vin": v.get("vin"),
                                "stock": v.get("stock"),
                                "year": v.get("year"),
                                "make": v.get("make"),
                                "model": v.get("model"),
                                "trim": v.get("trim"),
                                "submodel": v.get("submodel"),
                                "exterior_color": v.get("exterior_color"),
                                "msrp": v.get("msrp"),
                                "price": v.get("price"),
                                "mileage": v.get("mileage"),
                })

    return {
                "total": data.get("total", len(results)),
                "vehicles": results,
    }


class PaymentRequest(BaseModel):
        vin: Optional[str] = None
    stock: Optional[str] = None
    price: Optional[float] = None
    down_payment: float = 0
    term_months: int = 84
    deal_type: str = "finance"


@app.post("/get_vehicle_payment")
async def get_vehicle_payment(req: PaymentRequest):
        if not req.vin and not req.stock:
                    raise HTTPException(status_code=400, detail="VIN ou numero de stock requis.")

    inv_params: dict = {"deal_type": "cash", "request_vehicles": 1, "per_page": 1}
    if req.vin:
                inv_params["vin"] = req.vin
elif req.stock:
        inv_params["stock"] = req.stock

    inv_data = await roadster_get("/api/dealer_new_inventory", inv_params)
    vehicles = inv_data.get("inventory", [])
    if not vehicles:
                raise HTTPException(status_code=404, detail="Vehicule non trouve.")

    vehicle = vehicles[0]
    vin = vehicle.get("vin")
    price = req.price or vehicle.get("price") or vehicle.get("msrp") or 0

    payment_payload = {
                "vin": vin,
                "price": price,
                "down_payment": req.down_payment,
                "term_months": req.term_months,
                "deal_type": req.deal_type,
    }

    payment_data = await roadster_post("/api/calc/payment", payment_payload)

    return {
                "vehicle": {
                                "vin": vin,
                                "year": vehicle.get("year"),
                                "make": vehicle.get("make"),
                                "model": vehicle.get("model"),
                                "trim": vehicle.get("trim"),
                                "price": price,
                },
                "payment": payment_data,
    }


@app.get("/health")
async def health():
        return {"status": "ok"}
    
