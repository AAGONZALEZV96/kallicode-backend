import { describe, expect, it } from "vitest";

import { sanearUrl } from "./urls";

describe("sanearUrl", () => {
  it.each([
    ["https://github.com/demo/app/pull/479", "https://github.com/demo/app/pull/479"],
    [
      "http://gitlab.interno/demo/-/merge_requests/3",
      "http://gitlab.interno/demo/-/merge_requests/3",
    ],
    ["HTTPS://GitHub.com/demo", "https://github.com/demo"],
    ["  https://github.com/demo  ", "https://github.com/demo"],
  ])("acepta %s", (entrada, esperada) => {
    expect(sanearUrl(entrada)).toBe(esperada);
  });

  it.each([
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "java\tscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "ftp://github.com/x",
    "//malo.example/robar",
    "/tickets/KC-1001",
    "github.com/demo",
    "https://github.com@malo.example/robar",
    "https://usuario:clave@github.com/demo",
    "",
    null,
    undefined,
  ])("rechaza %s", (entrada) => {
    expect(sanearUrl(entrada)).toBeNull();
  });
});
