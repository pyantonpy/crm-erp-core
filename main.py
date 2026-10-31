from enum import Enum
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import Column, Enum as SQLEnum, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# --- 1. БАЗА ДАННЫХ И МОДЕЛИ ---
DATABASE_URL = "sqlite:///./app.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class StageEnum(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    WON = "WON"
    LOST = "LOST"

# ERP: Складской товар
class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    stock = Column(Integer, nullable=False)

# CRM: Сделка
class Deal(Base):
    __tablename__ = "deals"
    id = Column(Integer, primary_key=True, index=True)
    client_name = Column(String, nullable=False)
    client_phone = Column(String, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"))
    quantity = Column(Integer, default=1)
    stage = Column(SQLEnum(StageEnum), default=StageEnum.NEW)
    total_amount = Column(Float, default=0.0)
    loss_reason = Column(String, nullable=True)

    product = relationship("Product")

Base.metadata.create_all(bind=engine)

# Наполнение тестовыми данными при первом старте
def init_db():
    db = SessionLocal()
    if db.query(Product).count() == 0:
        db.add_all([
            Product(name="Ноутбук Pro 15", price=85000.0, stock=5),
            Product(name="Беспроводная мышь", price=2500.0, stock=20),
            Product(name="Механическая клавиатура", price=7000.0, stock=8),
        ])
        db.commit()
    db.close()

init_db()

# --- 2. СХЕМЫ ЗАПРОСОВ (PYDANTIC) ---
class DealCreate(BaseModel):
    client_name: str
    client_phone: str
    product_id: int
    quantity: int

class DealStageUpdate(BaseModel):
    stage: StageEnum
    loss_reason: Optional[str] = None

# --- 3. FASTAPI ПРИЛОЖЕНИЕ И ЭНДПОИНТЫ ---
app = FastAPI(title="Mini-CRM / ERP")

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")

@app.get("/api/products")
def get_products():
    db = SessionLocal()
    products = db.query(Product).all()
    db.close()
    return products

@app.get("/api/deals")
def get_deals():
    db = SessionLocal()
    deals = db.query(Deal).all()
    result = []
    for d in deals:
        result.append({
            "id": d.id,
            "client_name": d.client_name,
            "client_phone": d.client_phone,
            "product_name": d.product.name if d.product else "Удален",
            "quantity": d.quantity,
            "total_amount": d.total_amount,
            "stage": d.stage,
            "loss_reason": d.loss_reason
        })
    db.close()
    return result

@app.post("/api/deals")
def create_deal(payload: DealCreate):
    db = SessionLocal()
    product = db.query(Product).filter(Product.id == payload.product_id).first()
    if not product:
        db.close()
        raise HTTPException(status_code=404, detail="Товар не найден")

    total = product.price * payload.quantity
    deal = Deal(
        client_name=payload.client_name,
        client_phone=payload.client_phone,
        product_id=product.id,
        quantity=payload.quantity,
        stage=StageEnum.NEW,
        total_amount=total
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)
    db.close()
    return {"status": "created", "deal_id": deal.id}

@app.patch("/api/deals/{deal_id}/stage")
def update_stage(deal_id: int, payload: DealStageUpdate):
    db = SessionLocal()
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        db.close()
        raise HTTPException(status_code=404, detail="Сделка не найдена")

    # Бизнес-логика CRM: если проиграли — причина обязательна
    if payload.stage == StageEnum.LOST and not payload.loss_reason:
        db.close()
        raise HTTPException(status_code=400, detail="Укажите причину отказа!")

    # Интеграция с ERP: если сделка WON — списываем со склада
    if payload.stage == StageEnum.WON and deal.stage != StageEnum.WON:
        product = db.query(Product).filter(Product.id == deal.product_id).first()
        if product.stock < deal.quantity:
            db.close()
            raise HTTPException(
                status_code=400, 
                detail=f"ERP Ошибка: на складе осталось только {product.stock} шт., требуется {deal.quantity} шт."
            )
        product.stock -= deal.quantity

    deal.stage = payload.stage
    deal.loss_reason = payload.loss_reason if payload.stage == StageEnum.LOST else None
    db.commit()
    db.close()
    return {"status": "updated"}

# Аналитика CRM
@app.get("/api/analytics")
def get_analytics():
    db = SessionLocal()
    deals = db.query(Deal).all()
    db.close()

    total_deals = len(deals)
    won_deals = [d for d in deals if d.stage == StageEnum.WON]
    lost_deals = [d for d in deals if d.stage == StageEnum.LOST]

    actual_revenue = sum(d.total_amount for d in won_deals)

    # Вероятности для прогнозной выручки
    weights = {StageEnum.NEW: 0.1, StageEnum.IN_PROGRESS: 0.5, StageEnum.WON: 1.0, StageEnum.LOST: 0.0}
    forecast_revenue = sum(d.total_amount * weights[d.stage] for d in deals)

    closed_count = len(won_deals) + len(lost_deals)
    conversion = (len(won_deals) / closed_count * 100) if closed_count > 0 else 0.0

    return {
        "total_deals": total_deals,
        "actual_revenue": actual_revenue,
        "forecast_revenue": forecast_revenue,
        "conversion_rate": round(conversion, 1)
    }