from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.graph.builder import build_graph
from app.graph.state import LoanAgentState
from app.models import Application, ApplicationStatus
from sqlalchemy import select

logger = get_logger(__name__)


async def run_pipeline(application_id: str) -> None:
    import uuid

    try:
        app_uuid = uuid.UUID(application_id)
    except ValueError:
        logger.warning("pipeline.invalid_id", extra={"application_id": application_id})
        return
    async with SessionFactory() as db:
        result = await db.execute(select(Application).where(Application.id == app_uuid))
        application = result.scalar_one_or_none()
        if application is None:
            logger.warning("pipeline.not_found", extra={"application_id": application_id})
            return

        if application.status is ApplicationStatus.pending_docs:
            application.status = ApplicationStatus.processing
            await db.commit()

        graph = build_graph(db, application)
        initial_state: LoanAgentState = {"application_id": str(application.id)}
        final_state = await graph.ainvoke(initial_state)

        logger.info(
            "pipeline.completed",
            extra={
                "application_id": application_id,
                "outcome": final_state.get("outcome"),
                "degraded": final_state.get("degraded"),
                "facts": len(final_state.get("extracted_fact_ids", [])),
                "anomalies": len(final_state.get("anomalies", [])),
                "missing": len(final_state.get("missing_requirements", [])),
            },
        )