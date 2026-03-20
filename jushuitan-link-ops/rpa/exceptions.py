from __future__ import annotations


class LinkOpsError(Exception):
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


class PlatformSelectionError(LinkOpsError):
    pass


class ShopSelectionError(LinkOpsError):
    pass


class SearchResultEmptyError(LinkOpsError):
    pass


class SearchResultMismatchError(LinkOpsError):
    pass


class BatchDialogOpenError(LinkOpsError):
    pass


class OldCodeInputError(LinkOpsError):
    pass


class RowEditError(LinkOpsError):
    pass


class SubmitConfirmError(LinkOpsError):
    pass


class PostCheckFailedError(LinkOpsError):
    pass


class PageChangedError(LinkOpsError):
    pass


class ManualInputError(LinkOpsError):
    pass
