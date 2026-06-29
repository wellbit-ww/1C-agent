class ExcelAgentError(Exception):
    pass


class InvalidFileError(ExcelAgentError):
    pass


class EmptyDataFrameError(ExcelAgentError):
    pass


class OllamaUnavailableError(ExcelAgentError):
    pass
