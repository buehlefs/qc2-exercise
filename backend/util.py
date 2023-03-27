from base64 import b64encode


def text_to_data_url(text: str, content_type: str) -> str:
    """Generate a ``data:`` URL from the given string content.

    Args:
        text (str): the content to encode as a data URL
        content_type (str): the content type (mimetype) of the encoded data (e.g., "text/x-qasm" or "application/json")

    Returns:
        str: the encoded URL
    """
    return f"data:{content_type};base64,{b64encode(text.encode()).decode()}"
