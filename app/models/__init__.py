"""SQLAlchemy 모델. 새 모델을 만들면 여기서 import해야 Alembic이 인식한다."""
from app.models.analysis import Analysis  # noqa: F401
from app.models.app_space import AppSpace  # noqa: F401
from app.models.deployment import Deployment, DeploymentEvent, DeploymentResource  # noqa: F401
from app.models.infra_space import InfraSpace  # noqa: F401
from app.models.repository import Repository  # noqa: F401
