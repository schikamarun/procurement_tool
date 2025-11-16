"""Business services for the procurement evaluation tool."""
from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import bcrypt
import pandas as pd
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from models import Criterion, Offer, Project, ProjectEvaluator, Score, ScoreHistory, User, Vendor


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def authenticate_user(session: Session, username: str, password: str) -> Optional[User]:
    user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user and verify_password(password, user.password_hash):
        return user
    return None


def ensure_default_admin(session: Session) -> User:
    user = session.execute(select(User).where(User.username == "admin")).scalar_one_or_none()
    if user:
        return user
    default_password = "admin"
    user = User(
        username="admin",
        password_hash=get_password_hash(default_password),
        role="admin",
        display_name="Administrator",
    )
    session.add(user)
    session.commit()
    return user


# ---------------------------------------------------------------------------
# Project helpers
# ---------------------------------------------------------------------------


def list_projects(session: Session) -> List[Project]:
    return session.execute(select(Project).order_by(Project.created_at.desc())).scalars().all()


def upsert_project(
    session: Session,
    *,
    project_id: Optional[int],
    name: str,
    description: str,
    client: str,
    currency: str,
    quality_weight: float,
    price_weight: float,
    price_min: float,
    price_max: float,
    status: str,
) -> Project:
    if project_id:
        project = session.get(Project, project_id)
        if not project:
            raise ValueError("Projekt wurde nicht gefunden.")
    else:
        project = Project(created_at=dt.datetime.utcnow())
        session.add(project)

    project.name = name.strip()
    project.description = description.strip() if description else None
    project.client = client.strip() if client else None
    project.currency = currency.strip() or "CHF"
    weights_sum = quality_weight + price_weight
    if weights_sum == 0:
        quality_weight = 0.7
        price_weight = 0.3
        weights_sum = 1.0
    project.quality_weight = quality_weight / weights_sum
    project.price_weight = price_weight / weights_sum
    project.price_min_for_scoring = price_min
    project.price_max_for_scoring = price_max
    project.status = status
    return project


def project_to_dict(project: Project) -> Dict[str, object]:
    return {
        "ID": project.id,
        "Name": project.name,
        "Auftraggeber": project.client or "",
        "Status": project.status,
        "Qualitätsgewicht": project.quality_weight,
        "Preisgewicht": project.price_weight,
        "Währung": project.currency,
    }


# ---------------------------------------------------------------------------
# Criteria helpers
# ---------------------------------------------------------------------------


def list_project_criteria(session: Session, project_id: int) -> List[Criterion]:
    return (
        session.execute(select(Criterion).where(Criterion.project_id == project_id).order_by(Criterion.code))
        .scalars()
        .all()
    )


def upsert_criterion(
    session: Session,
    *,
    criterion_id: Optional[int],
    project_id: int,
    code: str,
    title: str,
    description: str,
    category: str,
    is_mandatory: bool,
    weight: float,
) -> Criterion:
    if criterion_id:
        criterion = session.get(Criterion, criterion_id)
        if not criterion:
            raise ValueError("Kriterium nicht gefunden.")
    else:
        criterion = Criterion(project_id=project_id)
        session.add(criterion)

    criterion.code = code.strip()
    criterion.title = title.strip()
    criterion.description = description.strip() if description else None
    criterion.category = category.strip() if category else None
    criterion.is_mandatory = bool(is_mandatory)
    criterion.weight = float(weight) if weight else 1.0
    return criterion


# ---------------------------------------------------------------------------
# Offer helpers
# ---------------------------------------------------------------------------


def list_project_offers(session: Session, project_id: int) -> List[Offer]:
    return (
        session.execute(select(Offer).where(Offer.project_id == project_id).order_by(Offer.id))
        .scalars()
        .all()
    )


def get_or_create_vendor(session: Session, name: str) -> Vendor:
    vendor = session.execute(select(Vendor).where(func.lower(Vendor.name) == name.lower())).scalar_one_or_none()
    if vendor:
        return vendor
    vendor = Vendor(name=name)
    session.add(vendor)
    session.flush()
    return vendor


def upsert_offer(
    session: Session,
    *,
    offer_id: Optional[int],
    project_id: int,
    vendor_name: str,
    offer_name: str,
    total_price: float,
    price_comment: str,
) -> Offer:
    vendor = get_or_create_vendor(session, vendor_name.strip())
    if offer_id:
        offer = session.get(Offer, offer_id)
        if not offer:
            raise ValueError("Angebot nicht gefunden.")
    else:
        offer = Offer(project_id=project_id, vendor_id=vendor.id)
        session.add(offer)

    offer.vendor_id = vendor.id
    offer.name = offer_name.strip()
    offer.total_price = float(total_price)
    offer.price_comment = price_comment.strip() if price_comment else None
    return offer


# ---------------------------------------------------------------------------
# Evaluator helpers
# ---------------------------------------------------------------------------


def list_users(session: Session) -> List[User]:
    return session.execute(select(User).order_by(User.display_name)).scalars().all()


def list_project_evaluators(session: Session, project_id: int) -> List[ProjectEvaluator]:
    return (
        session.execute(select(ProjectEvaluator).where(ProjectEvaluator.project_id == project_id))
        .scalars()
        .all()
    )


def set_project_evaluators(session: Session, project_id: int, user_ids: Sequence[int]) -> None:
    existing = list_project_evaluators(session, project_id)
    existing_ids = {pe.user_id: pe for pe in existing}

    for user_id in user_ids:
        if user_id not in existing_ids:
            session.add(ProjectEvaluator(project_id=project_id, user_id=user_id))

    for user_id, evaluator in existing_ids.items():
        if user_id not in user_ids:
            session.delete(evaluator)


# ---------------------------------------------------------------------------
# Scoring and aggregation
# ---------------------------------------------------------------------------


@dataclass
class CriterionScoreStats:
    criterion_id: int
    code: str
    title: str
    category: str
    is_mandatory: bool
    weight: float
    average: float
    minimum: Optional[float]
    maximum: Optional[float]
    count: int


@dataclass
class OfferAggregate:
    offer_id: int
    vendor_name: str
    offer_name: str
    total_price: float
    price_score: float
    quality_score: float
    overall_score: float
    mandatory_issues: int
    criterion_stats: List[CriterionScoreStats]


def _normalise_weights(criteria: List[Criterion]) -> Dict[int, float]:
    total_weight = sum(c.weight for c in criteria) or len(criteria) or 1.0
    return {c.id: (c.weight if total_weight else 1.0) / total_weight for c in criteria}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def compute_price_score(project: Project, offer: Offer) -> float:
    min_price = project.price_min_for_scoring
    max_price = project.price_max_for_scoring
    if max_price <= min_price:
        return 3.0
    price = offer.total_price
    if price <= min_price:
        return 5.0
    if price >= max_price:
        return 1.0
    ratio = (price - min_price) / (max_price - min_price)
    return _clamp(5.0 - 4.0 * ratio, 1.0, 5.0)


def compute_project_aggregates(session: Session, project_id: int) -> Tuple[Project, List[OfferAggregate]]:
    project = session.get(Project, project_id)
    if not project:
        raise ValueError("Projekt nicht gefunden.")

    criteria = list_project_criteria(session, project_id)
    offers = list_project_offers(session, project_id)
    if not offers:
        return project, []

    weight_map = _normalise_weights(criteria)
    scores = (
        session.execute(
            select(Score).where(Score.project_id == project_id)
        )
        .scalars()
        .all()
    )

    grouped: Dict[Tuple[int, int], List[Score]] = {}
    for score in scores:
        grouped.setdefault((score.offer_id, score.criterion_id), []).append(score)

    aggregates: List[OfferAggregate] = []
    for offer in offers:
        criterion_stats: List[CriterionScoreStats] = []
        quality_score = 0.0
        mandatory_issues = 0
        for criterion in criteria:
            values = grouped.get((offer.id, criterion.id), [])
            if values:
                scores_values = [s.score_value for s in values]
                average = sum(scores_values) / len(scores_values)
                minimum = min(scores_values)
                maximum = max(scores_values)
                count = len(scores_values)
                mandatory_issues += sum(1 for s in values if criterion.is_mandatory and s.mandatory_flag)
            else:
                average = 0.0
                minimum = None
                maximum = None
                count = 0
            quality_score += average * weight_map.get(criterion.id, 0.0)
            criterion_stats.append(
                CriterionScoreStats(
                    criterion_id=criterion.id,
                    code=criterion.code,
                    title=criterion.title,
                    category=criterion.category or "",
                    is_mandatory=criterion.is_mandatory,
                    weight=weight_map.get(criterion.id, 0.0),
                    average=round(average, 2) if count else 0.0,
                    minimum=minimum,
                    maximum=maximum,
                    count=count,
                )
            )
        price_score = compute_price_score(project, offer)
        overall_score = quality_score * project.quality_weight + price_score * project.price_weight
        aggregates.append(
            OfferAggregate(
                offer_id=offer.id,
                vendor_name=offer.vendor.name,
                offer_name=offer.name,
                total_price=offer.total_price,
                price_score=round(price_score, 2),
                quality_score=round(quality_score, 2),
                overall_score=round(overall_score, 2),
                mandatory_issues=mandatory_issues,
                criterion_stats=criterion_stats,
            )
        )

    aggregates.sort(key=lambda a: a.overall_score, reverse=True)
    return project, aggregates


# ---------------------------------------------------------------------------
# Score persistence and history
# ---------------------------------------------------------------------------


def save_scores(
    session: Session,
    *,
    evaluator: User,
    project_id: int,
    offer_id: int,
    entries: Iterable[Dict[str, object]],
) -> None:
    now = dt.datetime.utcnow()
    for entry in entries:
        criterion_id = int(entry["criterion_id"])
        score_value = int(entry["score_value"])
        comment = str(entry.get("comment", "")).strip()
        if not comment:
            raise ValueError("Kommentar ist Pflicht.")
        if score_value < 1 or score_value > 5:
            raise ValueError("Score muss zwischen 1 und 5 liegen.")
        mandatory_flag = bool(entry.get("mandatory_flag", False))

        score = session.execute(
            select(Score).where(
                Score.project_id == project_id,
                Score.offer_id == offer_id,
                Score.criterion_id == criterion_id,
                Score.evaluator_id == evaluator.id,
            )
        ).scalar_one_or_none()

        if score is None:
            score = Score(
                project_id=project_id,
                offer_id=offer_id,
                criterion_id=criterion_id,
                evaluator_id=evaluator.id,
                score_value=score_value,
                comment=comment,
                mandatory_flag=mandatory_flag,
                created_at=now,
                updated_at=now,
            )
            session.add(score)
        else:
            if (
                score.score_value != score_value
                or score.comment != comment
                or score.mandatory_flag != mandatory_flag
            ):
                history = ScoreHistory(
                    score_id=score.id,
                    changed_at=now,
                    changed_by=evaluator.id,
                    old_score_value=score.score_value,
                    new_score_value=score_value,
                    old_comment=score.comment,
                    new_comment=comment,
                )
                session.add(history)
            score.score_value = score_value
            score.comment = comment
            score.mandatory_flag = mandatory_flag
            score.updated_at = now


# ---------------------------------------------------------------------------
# Excel import/export utilities
# ---------------------------------------------------------------------------


CRITERIA_COLUMNS = ["Code", "Titel", "Beschreibung", "Kategorie", "MUSS", "Gewicht"]
OFFERS_COLUMNS = ["Firma", "Angebotsname", "Preis", "Preis-Kommentar"]


def read_criteria_preview(file_obj: io.BytesIO) -> pd.DataFrame:
    df = pd.read_excel(file_obj, sheet_name="Kriterien")
    missing = [col for col in CRITERIA_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Fehlende Spalten: {', '.join(missing)}")
    df = df[CRITERIA_COLUMNS].copy()
    df["MUSS"] = df["MUSS"].fillna("Nein")
    return df


def import_criteria_from_dataframe(session: Session, project_id: int, df: pd.DataFrame) -> int:
    count = 0
    for _, row in df.iterrows():
        if not str(row["Code"]).strip():
            continue
        criterion = Criterion(
            project_id=project_id,
            code=str(row["Code"]).strip(),
            title=str(row["Titel"]).strip(),
            description=str(row.get("Beschreibung", "") or "").strip() or None,
            category=str(row.get("Kategorie", "") or "").strip() or None,
            is_mandatory=str(row.get("MUSS", "Nein")).strip().lower() == "ja",
            weight=float(row.get("Gewicht", 1.0) or 1.0),
        )
        session.add(criterion)
        count += 1
    return count


def read_offers_preview(file_obj: io.BytesIO) -> pd.DataFrame:
    df = pd.read_excel(file_obj, sheet_name="Angebote")
    missing = [col for col in OFFERS_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Fehlende Spalten: {', '.join(missing)}")
    df = df[OFFERS_COLUMNS].copy()
    return df


def import_offers_from_dataframe(session: Session, project_id: int, df: pd.DataFrame) -> int:
    count = 0
    for _, row in df.iterrows():
        vendor_name = str(row["Firma"]).strip()
        if not vendor_name:
            continue
        vendor = get_or_create_vendor(session, vendor_name)
        offer = Offer(
            project_id=project_id,
            vendor_id=vendor.id,
            name=str(row["Angebotsname"]).strip(),
            total_price=float(row.get("Preis", 0.0) or 0.0),
            price_comment=str(row.get("Preis-Kommentar", "") or "").strip() or None,
        )
        session.add(offer)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Excel exports
# ---------------------------------------------------------------------------


def export_ranking_to_excel(project: Project, aggregates: List[OfferAggregate]) -> bytes:
    rows = []
    for idx, agg in enumerate(aggregates, start=1):
        rows.append(
            {
                "Rang": idx,
                "Anbieter": agg.vendor_name,
                "Angebotsname": agg.offer_name,
                "Gesamt-Score": agg.overall_score,
                "Qualitäts-Score": agg.quality_score,
                "Preis": agg.total_price,
                "Preis-Score": agg.price_score,
                "MUSS-Flags": agg.mandatory_issues,
            }
        )
    df = pd.DataFrame(rows)
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return output.getvalue()


def export_detailed_scores_to_excel(
    session: Session, project: Project, aggregates: List[OfferAggregate]
) -> bytes:
    offer_lookup = {agg.offer_id: agg for agg in aggregates}
    query = (
        select(
            Offer.id,
            Vendor.name,
            Offer.name,
            Criterion.code,
            Criterion.title,
            User.display_name,
            Score.score_value,
            Score.comment,
            Criterion.is_mandatory,
            Score.mandatory_flag,
        )
        .join(Offer.vendor)
        .join(Score, Score.offer_id == Offer.id)
        .join(Criterion, Criterion.id == Score.criterion_id)
        .join(User, User.id == Score.evaluator_id)
        .where(Offer.project_id == project.id)
        .order_by(Vendor.name, Offer.name, Criterion.code, User.display_name)
    )
    rows = []
    for result in session.execute(query):
        offer_id, vendor_name, offer_name, code, title, evaluator_name, score_value, comment, is_mandatory, mandatory_flag = result
        rows.append(
            {
                "Anbieter": vendor_name,
                "Angebotsname": offer_name,
                "Kriterium-Code": code,
                "Kriterium-Titel": title,
                "Evaluator": evaluator_name,
                "Score": score_value,
                "Kommentar": comment,
                "MUSS-Kriterium": "Ja" if is_mandatory else "Nein",
                "MUSS-Flag": "Ja" if mandatory_flag else "Nein",
            }
        )
    df = pd.DataFrame(rows)
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return output.getvalue()
