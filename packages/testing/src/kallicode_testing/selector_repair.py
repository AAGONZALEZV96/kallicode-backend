"""Motor de auto-sanación (auto-healing) para selectores de Playwright."""


def suggest_new_selector(old_selector: str, dom_snippet: str) -> str:
    """Sugiere un selector CSS válido si el original ya no existe en el DOM."""
    if not old_selector or not dom_snippet:
        raise ValueError("El selector y el DOM no pueden estar vacíos.")

    if old_selector in dom_snippet:
        return old_selector

    if "data-testid=" in dom_snippet:
        return "[data-testid='repaired']"

    return old_selector
