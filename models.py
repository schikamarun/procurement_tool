"""SQLAlchemy ORM models for the procurement evaluation tool."""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)

    evaluator_projects: Mapped[List["ProjectEvaluator"]] = relationship(
        "ProjectEvaluator", back_populates="user", cascade="all, delete-orphan"
    )
    scores: Mapped[List["Score"]] = relationship("Score", back_populates="evaluator")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    client: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    status: Mapped[str] = mapped_column(String(50), default="Entwurf")
    price_weight: Mapped[float] = mapped_column(Float, default=0.3)
    quality_weight: Mapped[float] = mapped_column(Float, default=0.7)
    currency: Mapped[str] = mapped_column(String(10), default="CHF")
    price_min_for_scoring: Mapped[float] = mapped_column(Float, default=0.0)
    price_max_for_scoring: Mapped[float] = mapped_column(Float, default=0.0)

    criteria: Mapped[List["Criterion"]] = relationship(
        "Criterion", back_populates="project", cascade="all, delete-orphan"
    )
    offers: Mapped[List["Offer"]] = relationship(
        "Offer", back_populates="project", cascade="all, delete-orphan"
    )
    evaluators: Mapped[List["ProjectEvaluator"]] = relationship(
        "ProjectEvaluator", back_populates="project", cascade="all, delete-orphan"
    )


class Criterion(Base):
    __tablename__ = "criteria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    project: Mapped[Project] = relationship("Project", back_populates="criteria")
    scores: Mapped[List["Score"]] = relationship("Score", back_populates="criterion")


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    contact_info: Mapped[Optional[str]] = mapped_column(Text)

    offers: Mapped[List["Offer"]] = relationship("Offer", back_populates="vendor")


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    total_price: Mapped[float] = mapped_column(Float, nullable=False)
    price_comment: Mapped[Optional[str]] = mapped_column(Text)
    submission_date: Mapped[Optional[dt.date]] = mapped_column(Date)

    project: Mapped[Project] = relationship("Project", back_populates="offers")
    vendor: Mapped[Vendor] = relationship("Vendor", back_populates="offers")
    scores: Mapped[List["Score"]] = relationship("Score", back_populates="offer")


class ProjectEvaluator(Base):
    __tablename__ = "project_evaluators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role_in_project: Mapped[Optional[str]] = mapped_column(String(100))

    project: Mapped[Project] = relationship("Project", back_populates="evaluators")
    user: Mapped[User] = relationship("User", back_populates="evaluator_projects")

    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_user"),)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"), nullable=False)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criteria.id"), nullable=False)
    evaluator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    score_value: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    mandatory_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    evaluator: Mapped[User] = relationship("User", back_populates="scores")
    criterion: Mapped[Criterion] = relationship("Criterion", back_populates="scores")
    offer: Mapped[Offer] = relationship("Offer", back_populates="scores")

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "offer_id",
            "criterion_id",
            "evaluator_id",
            name="uq_score_unique",
        ),
    )


class ScoreHistory(Base):
    __tablename__ = "score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(ForeignKey("scores.id"), nullable=False)
    changed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    old_score_value: Mapped[Optional[int]] = mapped_column(Integer)
    new_score_value: Mapped[Optional[int]] = mapped_column(Integer)
    old_comment: Mapped[Optional[str]] = mapped_column(Text)
    new_comment: Mapped[Optional[str]] = mapped_column(Text)

    score: Mapped[Score] = relationship("Score")
    changer: Mapped[User] = relationship("User")
