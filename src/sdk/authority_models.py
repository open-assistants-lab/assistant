"""Immutable server-resolved authority; client payloads are not authenticated actors."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from src.sdk.run_models import CanonicalModel, ContractModel, NonEmptyString

OpaqueId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
InstanceMode = Literal["SL", "SR", "MT", "MU"]
CompanyRole = Literal["owner", "admin", "staff"]


class Actor(ContractModel):
    """Authenticated subject mapped to an instance's current membership."""

    instance_id: OpaqueId
    user_id: OpaqueId
    key_id: NonEmptyString | None
    role: CompanyRole
    scopes: tuple[NonEmptyString, ...] = ()
    membership_revision: int = Field(ge=1)


class InstancePolicy(ContractModel):
    """Instance configuration; None means unrestricted by this particular layer."""

    instance_id: OpaqueId
    revision: int = Field(default=1, ge=1)
    default_model: CanonicalModel
    allowed_models: tuple[CanonicalModel, ...] | None = None
    allowed_providers: tuple[NonEmptyString, ...] | None = None
    allowed_tools: tuple[NonEmptyString, ...] | None = None
    allowed_destinations: tuple[NonEmptyString, ...] | None = None
    operational_retention_days: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def permitted_default(self) -> "InstancePolicy":
        if self.allowed_models is not None and self.default_model not in self.allowed_models:
            raise ValueError("instance default is not an allowed model")
        provider = self.default_model.partition(":")[0]
        if self.allowed_providers is not None and provider not in self.allowed_providers:
            raise ValueError("instance default is not an allowed provider")
        return self
