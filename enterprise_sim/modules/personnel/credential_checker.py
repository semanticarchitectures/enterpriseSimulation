"""Credential checker for personnel enclave access and clearance validation.

Validates personnel credentials against enclave access requirements and
publishes appropriate authentication events via the EventBus.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
"""

from enterprise_sim.engine.event_bus import EventBus
from enterprise_sim.models.personnel import ClearanceLevel, Personnel
from enterprise_sim.models.personnel_events import (
    AccessDeniedEvent,
    AuthenticationSuccessEvent,
    ClearanceInsufficientEvent,
)


class CredentialChecker:
    """Validates personnel credentials against enclave access requirements."""

    def check_enclave_access(self, personnel: Personnel, enclave_name: str) -> bool:
        """Check if personnel's credential authorizes access to the named enclave.

        Returns True if the enclave_name is in the personnel's authorized_enclaves
        list, False otherwise.
        """
        return enclave_name in personnel.credential.authorized_enclaves

    def check_clearance_level(
        self, personnel: Personnel, required_level: ClearanceLevel
    ) -> bool:
        """Check if personnel's clearance meets or exceeds the required level.

        Uses IntEnum comparison: personnel clearance >= required level.
        Returns True if clearance is sufficient, False otherwise.
        """
        return personnel.credential.clearance_level >= required_level

    def authenticate(
        self,
        personnel: Personnel,
        enclave_name: str,
        enclave_classification: ClearanceLevel,
        event_bus: EventBus,
        timestamp: float,
    ) -> bool:
        """Authenticate personnel for enclave access.

        Combines enclave access check and clearance level check. Publishes
        the appropriate event based on the result:
          - AccessDeniedEvent if enclave not in authorized_enclaves
          - ClearanceInsufficientEvent if clearance_level < enclave_classification
          - AuthenticationSuccessEvent if both checks pass

        Returns True on successful authentication, False otherwise.
        """
        # Check enclave authorization first
        if not self.check_enclave_access(personnel, enclave_name):
            event_bus.publish(
                AccessDeniedEvent(
                    entity_id=f"access_denied_{personnel.name}_{enclave_name}_{timestamp}",
                    timestamp=timestamp,
                    personnel_name=personnel.name,
                    credential_type=personnel.credential.credential_type.value,
                    denied_enclave=enclave_name,
                )
            )
            return False

        # Check clearance level
        if not self.check_clearance_level(personnel, enclave_classification):
            event_bus.publish(
                ClearanceInsufficientEvent(
                    entity_id=f"clearance_insufficient_{personnel.name}_{enclave_name}_{timestamp}",
                    timestamp=timestamp,
                    personnel_name=personnel.name,
                    personnel_clearance=personnel.credential.clearance_level.name,
                    enclave_name=enclave_name,
                    required_classification=enclave_classification.name,
                )
            )
            return False

        # Both checks passed — authentication success
        event_bus.publish(
            AuthenticationSuccessEvent(
                entity_id=f"auth_success_{personnel.name}_{enclave_name}_{timestamp}",
                timestamp=timestamp,
                personnel_name=personnel.name,
                enclave_name=enclave_name,
            )
        )
        return True
