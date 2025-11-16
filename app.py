"""Gradio application entry point for the procurement evaluation tool."""
from __future__ import annotations

import io
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
import pandas as pd
from sqlalchemy import select

from database import get_session, init_db
from models import Criterion, Offer, Project, ProjectEvaluator, Score, User
from services import (
    authenticate_user,
    compute_project_aggregates,
    ensure_default_admin,
    export_detailed_scores_to_excel,
    export_ranking_to_excel,
    import_criteria_from_dataframe,
    import_offers_from_dataframe,
    list_project_criteria,
    list_project_offers,
    list_project_evaluators,
    list_projects,
    list_users,
    project_to_dict,
    read_criteria_preview,
    read_offers_preview,
    save_scores,
    set_project_evaluators,
    upsert_criterion,
    upsert_offer,
    upsert_project,
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def project_choices() -> List[Tuple[str, int]]:
    with get_session() as session:
        projects = list_projects(session)
    return [(f"{p.name} (ID {p.id})", p.id) for p in projects]


def _read_uploaded_file(file: Any) -> bytes:
    if hasattr(file, "read"):
        return file.read()
    if isinstance(file, dict) and "name" in file:
        with open(file["name"], "rb") as handle:
            return handle.read()
    if isinstance(file, str):
        with open(file, "rb") as handle:
            return handle.read()
    raise ValueError("Unbekanntes Dateiobjekt")


def _prepare_download(data: bytes, filename: str) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}")
    tmp.write(data)
    tmp.flush()
    tmp.close()
    return tmp.name


def project_dataframe() -> pd.DataFrame:
    with get_session() as session:
        projects = list_projects(session)
        data = [project_to_dict(p) for p in projects]
    return pd.DataFrame(data)


def criterion_choices(project_id: Optional[int]) -> List[Tuple[str, int]]:
    if not project_id:
        return []
    with get_session() as session:
        criteria = list_project_criteria(session, int(project_id))
    return [(f"{c.code} - {c.title}", c.id) for c in criteria]


def offer_choices(project_id: Optional[int]) -> List[Tuple[str, int]]:
    if not project_id:
        return []
    with get_session() as session:
        offers = list_project_offers(session, int(project_id))
    return [(f"{o.vendor.name} - {o.name}", o.id) for o in offers]


def offers_dataframe(project_id: Optional[int]) -> pd.DataFrame:
    if not project_id:
        return pd.DataFrame()
    with get_session() as session:
        offers = list_project_offers(session, int(project_id))
        rows = [
            {
                "ID": o.id,
                "Anbieter": o.vendor.name,
                "Angebotsname": o.name,
                "Preis": o.total_price,
                "Kommentar": o.price_comment or "",
            }
            for o in offers
        ]
    return pd.DataFrame(rows)


def criteria_dataframe(project_id: Optional[int]) -> pd.DataFrame:
    if not project_id:
        return pd.DataFrame()
    with get_session() as session:
        criteria = list_project_criteria(session, int(project_id))
        rows = [
            {
                "ID": c.id,
                "Code": c.code,
                "Titel": c.title,
                "Kategorie": c.category or "",
                "MUSS": "Ja" if c.is_mandatory else "Nein",
                "Gewicht": c.weight,
            }
            for c in criteria
        ]
    return pd.DataFrame(rows)


def project_users_dataframe(project_id: Optional[int]) -> pd.DataFrame:
    if not project_id:
        return pd.DataFrame()
    with get_session() as session:
        users = list_users(session)
        assigned = {pe.user_id for pe in list_project_evaluators(session, int(project_id))}
        rows = [
            {
                "User-ID": user.id,
                "Benutzername": user.username,
                "Name": user.display_name,
                "Rolle": user.role,
                "Aktiv": user.id in assigned,
            }
            for user in users
        ]
    return pd.DataFrame(rows)


def evaluator_projects(user_id: int) -> List[Project]:
    with get_session() as session:
        projects = (
            session.execute(
                select(Project)
                .join(ProjectEvaluator, ProjectEvaluator.project_id == Project.id)
                .where(ProjectEvaluator.user_id == user_id)
                .order_by(Project.name)
            )
            .scalars()
            .all()
        )
    return projects


def evaluator_projects_dataframe(user_id: int) -> pd.DataFrame:
    projects = evaluator_projects(user_id)
    return pd.DataFrame([project_to_dict(p) for p in projects])


def scores_dataframe(project_id: Optional[int], offer_id: Optional[int], evaluator_id: int) -> pd.DataFrame:
    if not project_id or not offer_id:
        return pd.DataFrame()
    with get_session() as session:
        criteria = list_project_criteria(session, int(project_id))
        scores = (
            session.execute(
                select(Score).where(
                    Score.project_id == int(project_id),
                    Score.offer_id == int(offer_id),
                    Score.evaluator_id == int(evaluator_id),
                )
            )
            .scalars()
            .all()
        )
        score_map = {s.criterion_id: s for s in scores}
        rows = []
        for criterion in criteria:
            sc = score_map.get(criterion.id)
            rows.append(
                [
                    criterion.id,
                    criterion.code,
                    criterion.title,
                    sc.score_value if sc else 1,
                    sc.comment if sc else "",
                    bool(sc.mandatory_flag) if sc else False,
                    "Ja" if criterion.is_mandatory else "Nein",
                ]
            )
    return pd.DataFrame(
        rows,
        columns=[
            "Kriterium-ID",
            "Code",
            "Titel",
            "Score (1-5)",
            "Kommentar",
            "MUSS verletzt",
            "MUSS-Kriterium",
        ],
    )


def ranking_dataframe(project_id: Optional[int], hide_flags: bool) -> Tuple[pd.DataFrame, str]:
    if not project_id:
        return pd.DataFrame(), "Bitte Projekt wählen."
    with get_session() as session:
        project, aggregates = compute_project_aggregates(session, int(project_id))
    rows = []
    rank = 0
    for agg in aggregates:
        if hide_flags and agg.mandatory_issues:
            continue
        rank += 1
        rows.append(
            {
                "Rang": rank,
                "Anbieter": agg.vendor_name,
                "Angebotsname": agg.offer_name,
                "Qualitäts-Score": agg.quality_score,
                "Preis": agg.total_price,
                "Preis-Score": agg.price_score,
                "Gesamt-Score": agg.overall_score,
                "MUSS-Verstöße": agg.mandatory_issues,
            }
        )
    return pd.DataFrame(rows), f"{len(rows)} Angebote angezeigt."


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def login_handler(username: str, password: str, current_state: Dict) -> Tuple:
    if current_state:
        return gr.update(visible=False), gr.update(visible=True), current_state, ""
    with get_session() as session:
        user = authenticate_user(session, username, password)
    if not user:
        return (
            gr.update(visible=True),
            gr.update(visible=False),
            {},
            "Anmeldung fehlgeschlagen.",
        )
    state = {"id": user.id, "username": user.username, "role": user.role, "display_name": user.display_name}
    return gr.update(visible=False), gr.update(visible=True), state, f"Willkommen {user.display_name}!"


def logout_handler() -> Tuple:
    return gr.update(visible=True), gr.update(visible=False), {}, "Sie wurden abgemeldet."


def save_project_event(
    project_id,
    name,
    description,
    client,
    currency,
    quality_weight,
    price_weight,
    price_min,
    price_max,
    status,
):
    try:
        with get_session() as session:
            project = upsert_project(
                session,
                project_id=int(project_id) if project_id else None,
                name=name or "",
                description=description or "",
                client=client or "",
                currency=currency or "CHF",
                quality_weight=float(quality_weight or 0.7),
                price_weight=float(price_weight or 0.3),
                price_min=float(price_min or 0.0),
                price_max=float(price_max or 0.0),
                status=status or "Entwurf",
            )
            session.commit()
            project_id = project.id
        choices = project_choices()
    except Exception as exc:  # noqa: BLE001
        choices = project_choices()
        return (
            project_dataframe(),
            gr.Dropdown.update(choices=choices, value=project_id if project_id else None),
            gr.Dropdown.update(choices=choices, value=None),
            gr.Dropdown.update(choices=choices, value=None),
            gr.Dropdown.update(choices=choices, value=None),
            gr.Dropdown.update(choices=choices, value=None),
            gr.Dropdown.update(choices=choices, value=None),
            gr.Dropdown.update(choices=choices, value=None),
            gr.Dropdown.update(choices=choices, value=None),
            f"Fehler: {exc}",
        )
    choices = project_choices()
    return (
        project_dataframe(),
        gr.Dropdown.update(choices=choices, value=project_id),
        gr.Dropdown.update(choices=choices, value=None),
        gr.Dropdown.update(choices=choices, value=None),
        gr.Dropdown.update(choices=choices, value=None),
        gr.Dropdown.update(choices=choices, value=None),
        gr.Dropdown.update(choices=choices, value=None),
        gr.Dropdown.update(choices=choices, value=None),
        gr.Dropdown.update(choices=choices, value=None),
        "Projekt gespeichert.",
    )


def load_project_form_event(project_id):
    if not project_id:
        return ("", "", "", "CHF", 0.7, 0.3, 0.0, 0.0, "Entwurf")
    with get_session() as session:
        project = session.get(Project, int(project_id))
    if not project:
        return ("", "", "", "CHF", 0.7, 0.3, 0.0, 0.0, "Entwurf")
    return (
        project.name,
        project.description or "",
        project.client or "",
        project.currency or "CHF",
        project.quality_weight,
        project.price_weight,
        project.price_min_for_scoring,
        project.price_max_for_scoring,
        project.status,
    )


def save_criterion_event(project_id, criterion_id, code, title, description, category, is_mandatory, weight):
    if not project_id:
        return criteria_dataframe(None), gr.Dropdown.update(choices=[]), "Bitte zuerst ein Projekt wählen."
    try:
        with get_session() as session:
            criterion = upsert_criterion(
                session,
                criterion_id=int(criterion_id) if criterion_id else None,
                project_id=int(project_id),
                code=code or "",
                title=title or "",
                description=description or "",
                category=category or "",
                is_mandatory=bool(is_mandatory),
                weight=float(weight or 1.0),
            )
            session.commit()
            criterion_id = criterion.id
    except Exception as exc:  # noqa: BLE001
        return (
            criteria_dataframe(project_id),
            gr.Dropdown.update(choices=criterion_choices(project_id), value=None),
            f"Fehler: {exc}",
        )
    return (
        criteria_dataframe(project_id),
        gr.Dropdown.update(choices=criterion_choices(project_id), value=criterion_id),
        "Kriterium gespeichert.",
    )


def load_criterion_form_event(criterion_id):
    if not criterion_id:
        return ("", "", "", False, 1.0)
    with get_session() as session:
        criterion = session.get(Criterion, int(criterion_id))
    if not criterion:
        return ("", "", "", False, 1.0)
    return (
        criterion.code,
        criterion.title,
        criterion.description or "",
        bool(criterion.is_mandatory),
        criterion.weight,
    )


def save_offer_event(project_id, offer_id, vendor_name, offer_name, total_price, price_comment):
    if not project_id:
        return offers_dataframe(None), gr.Dropdown.update(choices=[]), "Bitte ein Projekt wählen."
    try:
        with get_session() as session:
            offer = upsert_offer(
                session,
                offer_id=int(offer_id) if offer_id else None,
                project_id=int(project_id),
                vendor_name=vendor_name or "",
                offer_name=offer_name or "",
                total_price=float(total_price or 0.0),
                price_comment=price_comment or "",
            )
            session.commit()
            offer_id = offer.id
    except Exception as exc:  # noqa: BLE001
        return (
            offers_dataframe(project_id),
            gr.Dropdown.update(choices=offer_choices(project_id), value=None),
            f"Fehler: {exc}",
        )
    return (
        offers_dataframe(project_id),
        gr.Dropdown.update(choices=offer_choices(project_id), value=offer_id),
        "Angebot gespeichert.",
    )


def load_offer_form_event(offer_id):
    if not offer_id:
        return ("", "", 0.0, "")
    with get_session() as session:
        offer = session.get(Offer, int(offer_id))
    if not offer:
        return ("", "", 0.0, "")
    return (offer.vendor.name, offer.name, offer.total_price, offer.price_comment or "")


def save_project_users_event(project_id, table):
    if not project_id:
        return project_users_dataframe(None), "Bitte Projekt wählen."
    active_ids: List[int] = []
    for row in table:
        try:
            if row[4]:
                active_ids.append(int(row[0]))
        except (IndexError, TypeError, ValueError):
            continue
    with get_session() as session:
        set_project_evaluators(session, int(project_id), active_ids)
        session.commit()
    return project_users_dataframe(project_id), "Zuordnung gespeichert."


def save_scores_event(project_id, offer_id, evaluator_state, table):
    if not evaluator_state:
        return pd.DataFrame(), "Bitte zuerst anmelden."
    evaluator_id = evaluator_state.get("id")
    if not project_id or not offer_id:
        return scores_dataframe(project_id, offer_id, evaluator_id), "Projekt und Angebot wählen."
    entries = []
    for row in table:
        try:
            criterion_id = int(row[0])
            score_val = int(row[3])
        except (TypeError, ValueError, IndexError):
            continue
        comment = str(row[4]) if row[4] is not None else ""
        mandatory_flag = bool(row[5]) if len(row) > 5 else False
        entries.append(
            {
                "criterion_id": criterion_id,
                "score_value": score_val,
                "comment": comment,
                "mandatory_flag": mandatory_flag,
            }
        )
    try:
        with get_session() as session:
            evaluator = session.get(User, evaluator_id)
            save_scores(
                session,
                evaluator=evaluator,
                project_id=int(project_id),
                offer_id=int(offer_id),
                entries=entries,
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        return scores_dataframe(project_id, offer_id, evaluator_id), f"Fehler: {exc}"
    return scores_dataframe(project_id, offer_id, evaluator_id), "Bewertungen gespeichert."


def export_ranking_event(project_id):
    if not project_id:
        return None
    with get_session() as session:
        project, aggregates = compute_project_aggregates(session, int(project_id))
    data = export_ranking_to_excel(project, aggregates)
    file_path = _prepare_download(data, "ranking.xlsx")
    return file_path


def export_detail_event(project_id):
    if not project_id:
        return None
    with get_session() as session:
        project, aggregates = compute_project_aggregates(session, int(project_id))
        data = export_detailed_scores_to_excel(session, project, aggregates)
    file_path = _prepare_download(data, "detailbewertungen.xlsx")
    return file_path


def preview_criteria_event(file, project_id):
    if not file or not project_id:
        return pd.DataFrame(), ""
    try:
        data = _read_uploaded_file(file)
        df = read_criteria_preview(io.BytesIO(data))
        return df, f"{len(df)} Kriterien geladen."
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), f"Fehler: {exc}"


def import_criteria_event(file, project_id):
    if not file or not project_id:
        return (
            criteria_dataframe(project_id),
            gr.Dropdown.update(choices=criterion_choices(project_id), value=None),
            "Bitte Datei und Projekt wählen.",
        )
    try:
        data = _read_uploaded_file(file)
        df = read_criteria_preview(io.BytesIO(data))
        with get_session() as session:
            count = import_criteria_from_dataframe(session, int(project_id), df)
            session.commit()
        return (
            criteria_dataframe(project_id),
            gr.Dropdown.update(choices=criterion_choices(project_id), value=None),
            f"{count} Kriterien importiert.",
        )
    except Exception as exc:  # noqa: BLE001
        return (
            criteria_dataframe(project_id),
            gr.Dropdown.update(choices=criterion_choices(project_id), value=None),
            f"Fehler: {exc}",
        )


def preview_offers_event(file, project_id):
    if not file or not project_id:
        return pd.DataFrame(), ""
    try:
        data = _read_uploaded_file(file)
        df = read_offers_preview(io.BytesIO(data))
        return df, f"{len(df)} Angebote geladen."
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), f"Fehler: {exc}"


def import_offers_event(file, project_id):
    if not file or not project_id:
        return (
            offers_dataframe(project_id),
            gr.Dropdown.update(choices=offer_choices(project_id), value=None),
            "Bitte Datei und Projekt wählen.",
        )
    try:
        data = _read_uploaded_file(file)
        df = read_offers_preview(io.BytesIO(data))
        with get_session() as session:
            count = import_offers_from_dataframe(session, int(project_id), df)
            session.commit()
        return (
            offers_dataframe(project_id),
            gr.Dropdown.update(choices=offer_choices(project_id), value=None),
            f"{count} Angebote importiert.",
        )
    except Exception as exc:  # noqa: BLE001
        return (
            offers_dataframe(project_id),
            gr.Dropdown.update(choices=offer_choices(project_id), value=None),
            f"Fehler: {exc}",
        )


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------


def build_interface() -> gr.Blocks:
    init_db()
    with get_session() as session:
        ensure_default_admin(session)

    with gr.Blocks(title="Beschaffungs-Bewertungstool") as demo:
        user_state = gr.State({})

        login_message = gr.Markdown("")
        with gr.Column(visible=True) as login_column:
            gr.Markdown("## Anmeldemaske")
            username = gr.Textbox(label="Benutzername")
            password = gr.Textbox(label="Passwort", type="password")
            login_button = gr.Button("Anmelden")

        with gr.Column(visible=False) as main_column:
            header = gr.Markdown("")
            logout_button = gr.Button("Abmelden")

            # Admin area -----------------------------------------------------
            with gr.Column(visible=False) as admin_column:
                with gr.Tabs():
                    with gr.TabItem("Projekte verwalten"):
                        project_table = gr.Dataframe(value=project_dataframe(), interactive=False)
                        project_selector = gr.Dropdown(label="Projekt auswählen", choices=project_choices())
                        name_in = gr.Textbox(label="Name")
                        description_in = gr.Textbox(label="Beschreibung", lines=3)
                        client_in = gr.Textbox(label="Auftraggeber")
                        currency_in = gr.Textbox(label="Währung", value="CHF")
                        quality_weight_in = gr.Number(label="Qualitätsgewicht", value=0.7)
                        price_weight_in = gr.Number(label="Preisgewicht", value=0.3)
                        price_min_in = gr.Number(label="Preis-Minimum", value=0.0)
                        price_max_in = gr.Number(label="Preis-Maximum", value=0.0)
                        status_in = gr.Dropdown(
                            label="Status",
                            choices=["Entwurf", "Bewertung", "Abgeschlossen"],
                            value="Entwurf",
                        )
                        save_project_btn = gr.Button("Projekt speichern")
                        project_info = gr.Markdown("")

                    with gr.TabItem("Kriterien verwalten"):
                        criteria_project = gr.Dropdown(label="Projekt", choices=project_choices())
                        criteria_table = gr.Dataframe(interactive=False)
                        criterion_selector = gr.Dropdown(label="Kriterium", choices=[])
                        code_in = gr.Textbox(label="Code")
                        title_in = gr.Textbox(label="Titel")
                        description_crit_in = gr.Textbox(label="Beschreibung", lines=3)
                        category_in = gr.Textbox(label="Kategorie")
                        mandatory_in = gr.Checkbox(label="MUSS-Kriterium")
                        weight_in = gr.Number(label="Gewicht", value=1.0)
                        save_criterion_btn = gr.Button("Kriterium speichern")
                        criteria_info = gr.Markdown("")
                        criteria_file = gr.File(label="Kriterien aus Excel importieren")
                        criteria_preview = gr.Dataframe(interactive=False)
                        import_criteria_btn = gr.Button("Import starten")

                    with gr.TabItem("Angebote verwalten"):
                        offers_project = gr.Dropdown(label="Projekt", choices=project_choices())
                        offers_table = gr.Dataframe(interactive=False)
                        offer_selector = gr.Dropdown(label="Angebot", choices=[])
                        vendor_in = gr.Textbox(label="Anbieter")
                        offer_name_in = gr.Textbox(label="Angebotsname")
                        total_price_in = gr.Number(label="Preis", value=0.0)
                        price_comment_in = gr.Textbox(label="Preis-Kommentar", lines=3)
                        save_offer_btn = gr.Button("Angebot speichern")
                        offer_info = gr.Markdown("")
                        offers_file = gr.File(label="Angebote aus Excel importieren")
                        offers_preview = gr.Dataframe(interactive=False)
                        import_offers_btn = gr.Button("Import starten")

                    with gr.TabItem("Bewerter-Zuordnung"):
                        evaluator_project = gr.Dropdown(label="Projekt", choices=project_choices())
                        evaluator_table = gr.Dataframe(interactive=True)
                        save_evaluator_btn = gr.Button("Zuordnung speichern")
                        evaluator_info = gr.Markdown("")

                    with gr.TabItem("Bewertung & Auswertung"):
                        eval_project = gr.Dropdown(label="Projekt", choices=project_choices())
                        eval_filter = gr.Checkbox(label="Nur Angebote ohne MUSS-Flag", value=False)
                        ranking_table = gr.Dataframe(interactive=False)
                        ranking_info = gr.Markdown("")

                    with gr.TabItem("Export"):
                        export_project = gr.Dropdown(label="Projekt", choices=project_choices())
                        export_ranking_btn = gr.Button("Ranking nach Excel exportieren")
                        export_detail_btn = gr.Button("Detailbewertungen nach Excel exportieren")
                        export_file = gr.File(label="Download", interactive=False)

            # Evaluator area -------------------------------------------------
            with gr.Column(visible=False) as evaluator_column:
                with gr.Tabs():
                    with gr.TabItem("Meine Projekte"):
                        evaluator_projects_table = gr.Dataframe(interactive=False)
                    with gr.TabItem("Bewertungen erfassen"):
                        eval_proj_dropdown = gr.Dropdown(label="Projekt")
                        eval_offer_dropdown = gr.Dropdown(label="Angebot")
                        scores_table = gr.Dataframe(interactive=True)
                        save_scores_btn = gr.Button("Zwischenspeichern")
                        scores_info = gr.Markdown("")

            # Viewer area ---------------------------------------------------
            with gr.Column(visible=False) as viewer_column:
                with gr.Tabs():
                    with gr.TabItem("Projekte"):
                        viewer_projects_table = gr.Dataframe(value=project_dataframe(), interactive=False)
                    with gr.TabItem("Auswertungen ansehen"):
                        viewer_project = gr.Dropdown(label="Projekt", choices=project_choices())
                        viewer_filter = gr.Checkbox(label="Nur Angebote ohne MUSS-Flag", value=False)
                        viewer_ranking_table = gr.Dataframe(interactive=False)
                        viewer_ranking_info = gr.Markdown("")

        # ------------------------------------------------------------------
        # Event wiring
        # ------------------------------------------------------------------

        login_button.click(
            login_handler,
            inputs=[username, password, user_state],
            outputs=[login_column, main_column, user_state, login_message],
        )

        logout_button.click(
            logout_handler,
            outputs=[login_column, main_column, user_state, login_message],
        )

        def update_role_visibility(user_dict):
            if not user_dict:
                return (
                    gr.Markdown.update(value=""),
                    gr.Column.update(visible=False),
                    gr.Column.update(visible=False),
                    gr.Column.update(visible=False),
                )
            header_text = f"Angemeldet als {user_dict['display_name']} ({user_dict['role']})"
            return (
                gr.Markdown.update(value=header_text),
                gr.Column.update(visible=user_dict["role"] == "admin"),
                gr.Column.update(visible=user_dict["role"] == "evaluator"),
                gr.Column.update(visible=user_dict["role"] == "viewer"),
            )

        user_state.change(
            update_role_visibility,
            inputs=user_state,
            outputs=[header, admin_column, evaluator_column, viewer_column],
        )

        # Admin bindings ----------------------------------------------------
        project_selector.change(load_project_form_event, inputs=project_selector, outputs=[
            name_in,
            description_in,
            client_in,
            currency_in,
            quality_weight_in,
            price_weight_in,
            price_min_in,
            price_max_in,
            status_in,
        ])

        save_project_btn.click(
            save_project_event,
            inputs=[
                project_selector,
                name_in,
                description_in,
                client_in,
                currency_in,
                quality_weight_in,
                price_weight_in,
                price_min_in,
                price_max_in,
                status_in,
            ],
            outputs=[
                project_table,
                project_selector,
                criteria_project,
                offers_project,
                evaluator_project,
                eval_project,
                export_project,
                viewer_project,
                eval_proj_dropdown,
                project_info,
            ],
        )

        def refresh_criteria_tab(project_id):
            return (
                criteria_dataframe(project_id),
                gr.Dropdown.update(choices=criterion_choices(project_id), value=None),
            )

        criteria_project.change(
            refresh_criteria_tab,
            inputs=criteria_project,
            outputs=[criteria_table, criterion_selector],
        )

        criterion_selector.change(
            load_criterion_form_event,
            inputs=criterion_selector,
            outputs=[code_in, title_in, description_crit_in, mandatory_in, weight_in],
        )

        save_criterion_btn.click(
            save_criterion_event,
            inputs=[
                criteria_project,
                criterion_selector,
                code_in,
                title_in,
                description_crit_in,
                category_in,
                mandatory_in,
                weight_in,
            ],
            outputs=[criteria_table, criterion_selector, criteria_info],
        )

        criteria_file.upload(
            preview_criteria_event,
            inputs=[criteria_file, criteria_project],
            outputs=[criteria_preview, criteria_info],
        )

        import_criteria_btn.click(
            import_criteria_event,
            inputs=[criteria_file, criteria_project],
            outputs=[criteria_table, criterion_selector, criteria_info],
        )

        def refresh_offers_tab(project_id):
            return (
                offers_dataframe(project_id),
                gr.Dropdown.update(choices=offer_choices(project_id), value=None),
            )

        offers_project.change(
            refresh_offers_tab,
            inputs=offers_project,
            outputs=[offers_table, offer_selector],
        )

        offer_selector.change(
            load_offer_form_event,
            inputs=offer_selector,
            outputs=[vendor_in, offer_name_in, total_price_in, price_comment_in],
        )

        save_offer_btn.click(
            save_offer_event,
            inputs=[
                offers_project,
                offer_selector,
                vendor_in,
                offer_name_in,
                total_price_in,
                price_comment_in,
            ],
            outputs=[offers_table, offer_selector, offer_info],
        )

        offers_file.upload(
            preview_offers_event,
            inputs=[offers_file, offers_project],
            outputs=[offers_preview, offer_info],
        )

        import_offers_btn.click(
            import_offers_event,
            inputs=[offers_file, offers_project],
            outputs=[offers_table, offer_selector, offer_info],
        )

        evaluator_project.change(
            project_users_dataframe,
            inputs=evaluator_project,
            outputs=evaluator_table,
        )

        save_evaluator_btn.click(
            save_project_users_event,
            inputs=[evaluator_project, evaluator_table],
            outputs=[evaluator_table, evaluator_info],
        )

        def refresh_ranking(project_id, hide_flags):
            df, info = ranking_dataframe(project_id, hide_flags)
            return df, info

        eval_project.change(
            refresh_ranking,
            inputs=[eval_project, eval_filter],
            outputs=[ranking_table, ranking_info],
        )
        eval_filter.change(
            refresh_ranking,
            inputs=[eval_project, eval_filter],
            outputs=[ranking_table, ranking_info],
        )

        export_ranking_btn.click(export_ranking_event, inputs=export_project, outputs=export_file)
        export_detail_btn.click(export_detail_event, inputs=export_project, outputs=export_file)

        # Evaluator bindings -----------------------------------------------
        def refresh_evaluator_data(user_dict):
            if not user_dict or user_dict.get("role") != "evaluator":
                return (
                    pd.DataFrame(),
                    gr.Dropdown.update(choices=[], value=None),
                    gr.Dropdown.update(choices=[], value=None),
                )
            projects = evaluator_projects(user_dict["id"])
            project_opts = [(f"{p.name} (ID {p.id})", p.id) for p in projects]
            first_project = project_opts[0][1] if project_opts else None
            offers_opts = offer_choices(first_project) if first_project else []
            return (
                evaluator_projects_dataframe(user_dict["id"]),
                gr.Dropdown.update(choices=project_opts, value=first_project),
                gr.Dropdown.update(choices=offers_opts, value=None),
            )

        user_state.change(
            refresh_evaluator_data,
            inputs=user_state,
            outputs=[evaluator_projects_table, eval_proj_dropdown, eval_offer_dropdown],
        )

        def refresh_eval_offers(project_id):
            return gr.Dropdown.update(choices=offer_choices(project_id), value=None)

        eval_proj_dropdown.change(
            refresh_eval_offers,
            inputs=eval_proj_dropdown,
            outputs=eval_offer_dropdown,
        )

        def refresh_scores(project_id, offer_id, user_dict):
            if not user_dict:
                return pd.DataFrame()
            return scores_dataframe(project_id, offer_id, user_dict["id"])

        eval_proj_dropdown.change(
            refresh_scores,
            inputs=[eval_proj_dropdown, eval_offer_dropdown, user_state],
            outputs=scores_table,
        )
        eval_offer_dropdown.change(
            refresh_scores,
            inputs=[eval_proj_dropdown, eval_offer_dropdown, user_state],
            outputs=scores_table,
        )

        save_scores_btn.click(
            save_scores_event,
            inputs=[eval_proj_dropdown, eval_offer_dropdown, user_state, scores_table],
            outputs=[scores_table, scores_info],
        )

        # Viewer bindings ---------------------------------------------------
        def refresh_viewer_projects(_user_dict):
            return project_dataframe()

        user_state.change(
            refresh_viewer_projects,
            inputs=user_state,
            outputs=viewer_projects_table,
        )

        def refresh_viewer_ranking(project_id, hide_flags):
            return ranking_dataframe(project_id, hide_flags)

        viewer_project.change(
            refresh_viewer_ranking,
            inputs=[viewer_project, viewer_filter],
            outputs=[viewer_ranking_table, viewer_ranking_info],
        )
        viewer_filter.change(
            refresh_viewer_ranking,
            inputs=[viewer_project, viewer_filter],
            outputs=[viewer_ranking_table, viewer_ranking_info],
        )

    return demo


if __name__ == "__main__":
    app = build_interface()
    app.launch()
