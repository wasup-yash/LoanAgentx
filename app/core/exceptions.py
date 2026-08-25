class LoanAgentError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    default_detail: str = "Unexpected internal error."

    def __init__(self, detail: str | None = None) -> None:
        resolved = detail or self.default_detail
        super().__init__(resolved)
        self.detail = resolved


class ApplicationNotFoundError(LoanAgentError):
    status_code = 404
    code = "application_not_found"
    default_detail = "Application does not exist."


class InvalidApplicationStateError(LoanAgentError):
    status_code = 409
    code = "invalid_application_state"
    default_detail = "Application is not in a state that permits this operation."


class PayloadTooLargeError(LoanAgentError):
    status_code = 413
    code = "payload_too_large"
    default_detail = "Attachment exceeds the maximum allowed size."


class UnsupportedMediaTypeError(LoanAgentError):
    status_code = 415
    code = "unsupported_media_type"
    default_detail = "Attachment file_type is not accepted."


class InvalidAttachmentError(LoanAgentError):
    status_code = 422
    code = "invalid_attachment"
    default_detail = "Attachment payload is missing or malformed."


class MalformedPDFError(LoanAgentError):
    status_code = 422
    code = "malformed_pdf"
    default_detail = "PDF could not be parsed."


class LLMRateLimitError(LoanAgentError):
    status_code = 429
    code = "llm_rate_limited"
    default_detail = "Upstream LLM provider rate limit hit."


class LLMTimeoutError(LoanAgentError):
    status_code = 504
    code = "llm_timeout"
    default_detail = "Upstream LLM call timed out."


class LLMResponseParseError(LoanAgentError):
    status_code = 502
    code = "llm_output_unparseable"
    default_detail = "LLM output failed structured parsing or source-trace validation."


class LLMConfigurationError(LoanAgentError):
    status_code = 500
    code = "llm_misconfigured"
    default_detail = "LLM provider is not configured correctly."


class LLMProviderError(LoanAgentError):
    status_code = 502
    code = "llm_provider_error"
    default_detail = "LLM provider is unreachable or returned an unexpected error."


class TextDecodingError(LoanAgentError):
    status_code = 422
    code = "text_decoding_failed"
    default_detail = "Text attachment is not valid UTF-8."


class OCRUnavailableError(LoanAgentError):
    status_code = 503
    code = "ocr_unavailable"
    default_detail = "OCR subsystem is unavailable or failed while processing the document."


class PIIRedactionError(LoanAgentError):
    status_code = 500
    code = "pii_redaction_failed"
    default_detail = "PII redaction could not be applied to the payload."


class LOSExportError(LoanAgentError):
    status_code = 502
    code = "los_export_failed"
    default_detail = "Core banking API rejected or dropped the export."
