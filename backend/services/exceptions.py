class ExcelAgentError(Exception):
    pass


class InvalidFileError(ExcelAgentError):
    pass


class EmptyDataFrameError(ExcelAgentError):
    pass


class OllamaUnavailableError(ExcelAgentError):
    pass


class WorkspaceError(ExcelAgentError):
    pass


class UnsupportedReportError(WorkspaceError):
    pass


class SlotOccupiedError(WorkspaceError):
    pass


class WorkspaceNotFoundError(WorkspaceError):
    pass
