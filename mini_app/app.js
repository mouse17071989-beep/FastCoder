(function () {
  const webApp = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (webApp) {
    webApp.ready();
    webApp.expand();
  }

  const statusEl = document.getElementById("status");
  const contentEl = document.getElementById("content");
  const metaEl = document.getElementById("meta");

  const params = new URLSearchParams(window.location.search);
  const id = params.get("id");

  if (!id) {
    statusEl.textContent = "Не найден параметр id.";
    return;
  }

  fetch(`/api/response?id=${encodeURIComponent(id)}`)
    .then((res) => {
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      return res.json();
    })
    .then((data) => {
      statusEl.textContent = "";
      contentEl.textContent = data.content || "(пусто)";
      if (data.created_at) {
        metaEl.textContent = `Сгенерировано: ${new Date(data.created_at).toLocaleString()}`;
      }
    })
    .catch(() => {
      statusEl.textContent = "Не удалось загрузить ответ.";
    });
})();
