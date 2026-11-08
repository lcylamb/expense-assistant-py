from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import get_settings
from .security import hash_password

DATABASE_URL = get_settings().database_url

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(20))
    department: Mapped[str] = mapped_column(String(40))
    password_hash: Mapped[str] = mapped_column(String(256))

    expenses: Mapped[list["Expense"]] = relationship(back_populates="applicant_user")


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(120))
    applicant: Mapped[str] = mapped_column(String(40))
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    department: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), index=True)
    reject_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    approver_name: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    approval_note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    applicant_user: Mapped[User] = relationship(back_populates="expenses")
    items: Mapped[list["ExpenseItem"]] = relationship(
        back_populates="expense", cascade="all, delete-orphan"
    )


class ExpenseItem(Base):
    __tablename__ = "expense_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    spent_on: Mapped[datetime] = mapped_column(DateTime)

    expense: Mapped[Expense] = relationship(back_populates="items")


def init_db(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        if session.query(User).count() == 0:
            seed(session)



def seed(session) -> None:
    password = hash_password("demo1234")
    zhangsan = User(username="zhangsan", name="张三", role="APPLICANT", department="销售部", password_hash=password)
    lisi = User(username="lisi", name="李四", role="APPLICANT", department="研发部", password_hash=password)
    wangwu = User(username="wangwu", name="王五", role="APPLICANT", department="市场部", password_hash=password)
    zhaoliu = User(username="zhaoliu", name="赵六", role="APPLICANT", department="财务部", password_hash=password)
    sunqi = User(username="sunqi", name="孙七", role="APPLICANT", department="人力资源部", password_hash=password)
    wangjl = User(username="wangjl", name="王经理", role="APPROVER", department="审批中心", password_hash=password)
    zhengshi = User(username="zhengshi", name="郑十", role="AUDITOR", department="内审部", password_hash=password)
    session.add_all([zhangsan, lisi, wangwu, zhaoliu, sunqi, wangjl, zhengshi])
    session.flush()

    def expense(code, title, user, category, amount, status, submitted, items, **extra):
        row = Expense(
            code=code,
            title=title,
            applicant=user.name,
            applicant_id=user.id,
            department=user.department,
            category=category,
            amount=amount,
            status=status,
            submitted_at=submitted,
            **extra,
        )
        row.items = [
            ExpenseItem(
                name=name,
                category=category,
                amount=item_amount,
                note=note,
                spent_on=spent_on,
            )
            for name, item_amount, note, spent_on in items
        ]
        return row

    rows = [
        expense(
            "BX-2026-0001", "北京客户现场支持差旅", zhangsan, "TRAVEL", 2860.0, "PAID",
            datetime(2026, 3, 3, 9, 0),
            [("高铁票", 1200.0, "深圳→北京", datetime(2026, 3, 1)), ("酒店", 1660.0, "2 晚", datetime(2026, 3, 1))],
            approver_name="王经理", approval_note="同意报销", decided_at=datetime(2026, 3, 5, 14, 0),
        ),
        expense(
            "BX-2026-0002", "研发部工位外设采购", lisi, "OFFICE", 1980.0, "APPROVED",
            datetime(2026, 3, 6, 10, 0),
            [("显示器", 1500.0, "27 寸", datetime(2026, 3, 5)), ("键盘", 480.0, None, datetime(2026, 3, 5))],
            approver_name="王经理", approval_note="同意报销", decided_at=datetime(2026, 3, 7, 11, 0),
        ),
        expense(
            "BX-2026-0003", "客户答谢晚宴", wangwu, "ENTERTAIN", 4200.0, "REJECTED",
            datetime(2026, 3, 10, 18, 0),
            [("晚宴餐费", 4200.0, "客户答谢", datetime(2026, 3, 8))],
            reject_reason="缺少参与人员清单与发票", approver_name="王经理", decided_at=datetime(2026, 3, 12, 15, 0),
        ),
        expense(
            "BX-2026-0004", "3 月市内客户拜访交通", zhaoliu, "TRANSPORT", 680.0, "SUBMITTED",
            datetime(2026, 3, 18, 17, 0),
            [("打车费", 680.0, "客户拜访", datetime(2026, 3, 17))],
        ),
        expense(
            "BX-2026-0005", "财务部办公耗材", zhaoliu, "OFFICE", 920.0, "DRAFT",
            datetime(2026, 3, 20, 9, 30),
            [("打印纸", 420.0, None, datetime(2026, 3, 19)), ("墨盒", 500.0, None, datetime(2026, 3, 19))],
        ),
        expense(
            "BX-2026-0006", "上海架构评审出差", lisi, "TRAVEL", 3450.0, "PAID",
            datetime(2026, 2, 20, 8, 30),
            [("高铁票", 1450.0, "深圳→上海", datetime(2026, 2, 18)), ("酒店", 2000.0, "2 晚", datetime(2026, 2, 18))],
            approver_name="王经理", approval_note="同意报销", decided_at=datetime(2026, 2, 22, 10, 0),
        ),
        expense(
            "BX-2026-0007", "季度团建餐费", zhangsan, "ENTERTAIN", 2600.0, "SUBMITTED",
            datetime(2026, 4, 1, 19, 0),
            [("团建餐费", 2600.0, "销售部季度团建", datetime(2026, 3, 28))],
        ),
        expense(
            "BX-2026-0008", "行业展会物料制作", wangwu, "OTHER", 5300.0, "APPROVED",
            datetime(2026, 4, 5, 11, 0),
            [("展架", 2300.0, None, datetime(2026, 4, 3)), ("印刷品", 3000.0, None, datetime(2026, 4, 3))],
            approver_name="王经理", approval_note="同意报销", decided_at=datetime(2026, 4, 7, 9, 0),
        ),
        expense(
            "BX-2026-0009", "杭州供应商走访差旅", lisi, "TRAVEL", 1780.0, "REJECTED",
            datetime(2026, 4, 10, 16, 0),
            [("高铁票", 780.0, "深圳→杭州", datetime(2026, 4, 8)), ("酒店", 1000.0, "1 晚", datetime(2026, 4, 8))],
            reject_reason="酒店发票抬头错误", approver_name="王经理", decided_at=datetime(2026, 4, 12, 14, 0),
        ),
        expense(
            "BX-2026-0010", "招聘平台年度续费", sunqi, "OTHER", 6800.0, "DRAFT",
            datetime(2026, 4, 15, 10, 0),
            [("招聘平台年费", 6800.0, "年度续费", datetime(2026, 4, 14))],
        ),
    ]
    session.add_all(rows)
    session.commit()
