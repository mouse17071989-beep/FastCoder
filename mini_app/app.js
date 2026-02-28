(function () {
  const webApp = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (webApp) {
    webApp.ready();
    webApp.expand();
  }

  const statusEl = document.getElementById("status");
  const explanationEl = document.getElementById("explanation");
  const codeBlockEl = document.getElementById("code-block");
  const codeEl = document.getElementById("code");
  const metaEl = document.getElementById("meta");

  const params = new URLSearchParams(window.location.search);
  const id = params.get("id");

  if (!id) {
    statusEl.textContent = "Не найден параметр id.";
    return;
  }

  const apiBase = window.MINI_APP_API_URL || "";
  const apiUrl = `${apiBase}/api/response?id=${encodeURIComponent(id)}`;

  const CODE_MARKER = "===CODE===";

  fetch(apiUrl)
    .then((res) => {
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      return res.json();
    })
    .then((data) => {
      statusEl.textContent = "";
      const content = data.content || "(пусто)";
      const markerIndex = content.indexOf(CODE_MARKER);

      let explanationText = content;
      let codeText = "";

      if (markerIndex >= 0) {
        explanationText = content.slice(0, markerIndex).trim();
        codeText = content.slice(markerIndex + CODE_MARKER.length).trim();
      }

      explanationEl.textContent = explanationText || "(пусто)";

      if (codeText) {
        if (window.hljs && window.hljs.highlightAuto) {
          const highlighted = window.hljs.highlightAuto(codeText).value;
          const lines = highlighted.split(/\n/);
          codeEl.innerHTML = lines
            .map((line) => `<span class="code-line">${line || " "}</span>`)
            .join("\n");
        } else {
          codeEl.textContent = codeText;
        }
        codeBlockEl.classList.remove("is-hidden");
      } else {
        codeBlockEl.classList.add("is-hidden");
      }
      if (data.created_at) {
        metaEl.textContent = `Сгенерировано: ${new Date(data.created_at).toLocaleString()}`;
      }
    })
    .catch(() => {
      statusEl.textContent = "Не удалось загрузить ответ.";
    });
})();
