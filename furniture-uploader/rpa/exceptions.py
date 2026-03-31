from __future__ import annotations


class UploaderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        step_name: str | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.step_name = step_name or ""
        self.error_type = error_type or self.__class__.__name__


class StoreSelectionError(UploaderError):
    pass


class ProductMatchNotFoundError(UploaderError):
    pass


class MatchCandidateInvalidError(UploaderError):
    pass


class TemplateSelectionError(UploaderError):
    pass


class PublishValidationError(UploaderError):
    pass


class PublishSubmitError(UploaderError):
    pass


class OfflineTaskError(UploaderError):
    pass


class OfflineTaskNotFoundError(OfflineTaskError):
    pass


class OfflineTaskStateError(OfflineTaskError):
    pass


class OfflineStoreMismatchError(OfflineTaskError):
    pass


class OfflineLoginRequiredError(OfflineTaskError):
    pass
