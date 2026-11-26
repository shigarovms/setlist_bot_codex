(() => {
  const root = document.documentElement;
  const hex = /^#[0-9a-f]{6}$/i;
  let scheme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  let background;

  try {
    const saved = JSON.parse(sessionStorage.getItem("setlistbot.start-theme"));
    if (["dark", "light"].includes(saved?.scheme) && typeof saved.background === "string"
      && hex.test(saved.background)) {
      ({ scheme, background } = saved);
    }
  } catch { /* Storage may be disabled in a web view. */ }

  if (!background) {
    try {
      const params = new URLSearchParams(location.hash.slice(1));
      const color = JSON.parse(params.get("tgWebAppThemeParams"))?.bg_color;
      if (typeof color === "string" && hex.test(color)) {
        background = color;
        const [red, green, blue] = color.slice(1).match(/../g).map(pair => parseInt(pair, 16));
        // Match the Telegram SDK's perceived-brightness test for custom themes.
        const brightness = 0.299 * red ** 2 + 0.587 * green ** 2 + 0.114 * blue ** 2;
        scheme = brightness < 120 ** 2 ? "dark" : "light";
      }
    } catch { /* Ignore malformed launch data. */ }
  }

  background ??= scheme === "dark" ? "#1c1c1e" : "#f5f5f7";
  root.dataset.theme = scheme;
  root.style.colorScheme = scheme;
  root.style.backgroundColor = background;
  root.style.setProperty("--bg", background);
})();
