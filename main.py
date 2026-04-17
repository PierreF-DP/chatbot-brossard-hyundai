from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import httpx
import asyncio

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
    "Connection": "keep-alive",
    "Referer": "https://brossard.shop.hyundaicanada.com/",
    "Origin": "https://brossard.shop.hyundaicanada.com",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


async def roadster_get(path: str, params: dict):
    url = BASE_URL + path
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                follow_redirects=True,
                timeout=20.0,
            ) as client:
                resp = await client.get(url, params=params)
            if resp.status_code == 429:
                if attempt < 2:
                    await asyncio.sleep(3)
                    continue
                raise HTTPException(status_code=429, detail="Trop de requetes. Reessayez dans quelques secondes.")
            if resp.status_code in (403, 503):
                raise HTTPException(status_code=503, detail="Cloudflare bloque.")
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Delai depasse.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=429, detail="Trop de requetes. Reessayez dans quelques secondes.")


async def roadster_post(path: str, payload: dict):
    url = BASE_URL + path
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                follow_redirects=True,
                timeout=20.0,
            ) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code == 429:
                if attempt < 2:
                    await asyncio.sleep(3)
                    continue
                raise HTTPException(status_code=429, detail="Trop de requetes. Reessayez dans quelques secondes.")
            if resp.status_code in (403, 503):
                raise HTTPException(status_code=503, detail="Cloudflare bloque.")
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Delai depasse.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=429, detail="Trop de requetes. Reessayez dans quelques secondes.")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/search_inventory")
async def search_inventory(
    submodel: Optional[str] = None,
    stock: Optional[str] = None,
    year: Optional[int] = None,
    trim: Optional[str] = None,
    color: Optional[str] = None,
    max_price: Optional[int] = None,
    limit: int = 5,
):
    params = {"per_page": limit}
    if submodel:
        params["submodel"] = submodel
    if stock:
        params["stock"] = stock
    if year:
        params["year"] = year
    if trim:
        params["trim"] = trim
    if color:
        params["color"] = color
    if max_price:
        params["max_price"] = max_price

    data = await roadster_get("/api/dealer_new_inventory", params)

    inventory = data.get("inventory", [])
    results = []
    for v in inventory[:limit]:
        results.append({
            "stock": v.get("stock_number"),
            "year": v.get("year"),
            "make": v.get("make"),
            "model": v.get("model"),
            "submodel": v.get("submodel"),
            "trim": v.get("trim"),
            "color": v.get("color"),
            "msrp": v.get("msrp"),
            "selling_price": v.get("selling_price"),
            "vin": v.get("vin"),
        })
    return {"count": len(results), "vehicles": results}


class PaymentRequest(BaseModel):
    stock_number: str
    down_payment: Optional[int] = 0
    trade_value: Optional[int] = 0
    months: Optional[int] = 60
    km_per_year: Optional[int] = 20000
    finance_type: Optional[str] = "finance"


@app.post("/get_vehicle_payment")
async def get_vehicle_payment(req: PaymentRequest):
    payload = {
        "stock_number": req.stock_number,
        "down_payment": req.down_payment,
        "trade_value": req.trade_value,
        "months": req.months,
        "km_per_year": req.km_per_year,
        "finance_type": req.finance_type,
    }
    data = await roadster_post("/api/calc/payment", payload)
    return data
