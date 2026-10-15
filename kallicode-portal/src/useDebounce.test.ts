import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ESPERA_DEBOUNCE_MS, useDebounce } from "./useDebounce";

describe("useDebounce", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const montar = () =>
    renderHook(({ valor }) => useDebounce(valor), { initialProps: { valor: "" } });

  it("espera 350 ms sin cambios antes de devolver el valor nuevo", () => {
    const { result, rerender } = montar();

    rerender({ valor: "fac" });
    act(() => vi.advanceTimersByTime(ESPERA_DEBOUNCE_MS - 1));
    expect(result.current).toBe("");

    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe("fac");
  });

  it("cada cambio reinicia la espera: mientras se escribe no cambia", () => {
    const { result, rerender } = montar();

    for (const valor of ["f", "fa", "fac", "fact"]) {
      rerender({ valor });
      act(() => vi.advanceTimersByTime(ESPERA_DEBOUNCE_MS - 50));
    }
    expect(result.current).toBe("");

    act(() => vi.advanceTimersByTime(50));
    expect(result.current).toBe("fact");
  });

  it("la espera por defecto es de 350 ms", () => {
    expect(ESPERA_DEBOUNCE_MS).toBe(350);
  });
});
