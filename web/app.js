(function () {
  "use strict";

  const messagesEl = document.getElementById("messages");
  const welcomeEl = document.getElementById("welcome");
  const formEl = document.getElementById("chat-form");
  const inputEl = document.getElementById("input");
  const sendEl = document.getElementById("send");
  const suggestionsEl = document.getElementById("suggestions");

  let streaming = false;
  let currentSource = null;

  // 会话ID：仅存在于当前页面内存中，刷新页面即重新生成 -> 记忆随之清空
  const SESSION_ID = "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);

  const BOT_AVATAR =
    '<svg viewBox="0 0 32 32" width="18" height="18">' +
    '<path d="M8 21c3.5-7 12.5-7 16 0" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round"></path>' +
    '<circle cx="16" cy="12" r="2.6" fill="#fff"></circle></svg>';

  /* ---------- 安全 Markdown 渲染（先转义再套用有限格式，避免 XSS） ---------- */
  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderMarkdown(src) {
    const codeBlocks = [];
    // 抽出代码块占位，避免内部内容被二次处理
    let text = src.replace(/```([\s\S]*?)```/g, (_, code) => {
      codeBlocks.push(code.replace(/^\n+|\n+$/g, ""));
      return "\u0000CODE" + (codeBlocks.length - 1) + "\u0000";
    });

    text = escapeHtml(text);

    // 行内代码、加粗、斜体
    text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

    // 链接（仅允许 http/https）
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

    // 按行处理列表与段落
    const lines = text.split("\n");
    let html = "";
    let listType = null;
    const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };

    for (let raw of lines) {
      const line = raw.trimEnd();
      const ul = line.match(/^\s*[-*]\s+(.*)$/);
      const ol = line.match(/^\s*\d+\.\s+(.*)$/);
      const h = line.match(/^(#{1,4})\s+(.*)$/);

      if (ul) {
        if (listType !== "ul") { closeList(); html += "<ul>"; listType = "ul"; }
        html += "<li>" + ul[1] + "</li>";
      } else if (ol) {
        if (listType !== "ol") { closeList(); html += "<ol>"; listType = "ol"; }
        html += "<li>" + ol[1] + "</li>";
      } else if (h) {
        closeList();
        html += "<strong class='md-h'>" + h[2] + "</strong>";
      } else if (line.trim() === "") {
        closeList();
      } else {
        closeList();
        html += "<p>" + line + "</p>";
      }
    }
    closeList();

    // 还原代码块
    html = html.replace(/\u0000CODE(\d+)\u0000/g, (_, i) =>
      "<pre><code>" + escapeHtml(codeBlocks[+i]) + "</code></pre>");

    return html;
  }

  /* ---------- 基础 DOM ---------- */
  function scrollToBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

  function hideWelcome() {
    if (welcomeEl && welcomeEl.parentNode) welcomeEl.remove();
  }

  function addRow(role) {
    const row = document.createElement("div");
    row.className = "row " + (role === "user" ? "row--user" : "row--bot");
    if (role === "bot") {
      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.innerHTML = BOT_AVATAR;
      row.appendChild(avatar);
    }
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    row.appendChild(bubble);
    messagesEl.appendChild(row);
    scrollToBottom();
    return { row, bubble };
  }

  /* 状态条：思考中 / 工具调用中 */
  let statusEl = null;
  function showStatus(text, thinking) {
    if (!statusEl) {
      statusEl = document.createElement("div");
      statusEl.className = "status-line";
      messagesEl.appendChild(statusEl);
    }
    if (thinking) {
      statusEl.innerHTML =
        '<span class="typing"><span></span><span></span><span></span></span><span>Aurora is thinking…</span>';
    } else {
      statusEl.innerHTML = '<span class="spinner"></span><span></span>';
      statusEl.lastChild.textContent = text;
    }
    scrollToBottom();
  }
  function clearStatus() {
    if (statusEl && statusEl.parentNode) statusEl.remove();
    statusEl = null;
  }

  /* ---------- 流式渲染状态 ---------- */
  let live = null; // { mid, raw, bubble, row, hadTool }

  function finalizeLive() {
    if (live && !live.hadTool && live.raw.trim()) {
      live.bubble.classList.remove("bubble--streaming");
      live.bubble.innerHTML = renderMarkdown(live.raw);
    }
    live = null;
  }

  function startLive(mid) {
    finalizeLive();
    clearStatus();
    const { row, bubble } = addRow("bot");
    bubble.classList.add("bubble--streaming");
    live = { mid, raw: "", bubble, row, hadTool: false };
  }

  function onToken(mid, content) {
    if (!live || live.mid !== mid) startLive(mid);
    live.raw += content;
    // 流式过程中用纯文本 + 光标，结束时再渲染 Markdown
    live.bubble.textContent = live.raw;
    scrollToBottom();
  }

  function onTool(mid, label) {
    // 该消息触发了工具 -> 属于“思考”，丢弃其流式文本，改为展示工具状态
    if (live && live.mid === mid) {
      live.hadTool = true;
      if (live.row && live.row.parentNode) live.row.remove();
      live = null;
    }
    showStatus((label || "Working") + "…", false);
  }

  /* ---------- 收发 ---------- */
  function setBusy(busy) {
    streaming = busy;
    sendEl.disabled = busy;
    inputEl.disabled = busy;
    if (!busy) { inputEl.focus(); autoGrow(); }
  }

  function send(text) {
    const query = (text || "").trim();
    if (!query || streaming) return;

    hideWelcome();
    const { bubble } = addRow("user");
    bubble.textContent = query;

    inputEl.value = "";
    autoGrow();
    setBusy(true);
    showStatus("", true);

    let gotAnswer = false;
    const url =
      "/api/chat?message=" + encodeURIComponent(query) +
      "&session=" + encodeURIComponent(SESSION_ID);
    const source = new EventSource(url);
    currentSource = source;

    const finish = () => {
      source.close();
      if (currentSource === source) currentSource = null;
      finalizeLive();
      clearStatus();
      setBusy(false);
    };

    source.onmessage = (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch (_) { return; }

      switch (data.type) {
        case "token":
          gotAnswer = true;
          onToken(data.mid, data.content || "");
          break;
        case "tool":
          onTool(data.mid, data.label);
          break;
        case "error":
          gotAnswer = true;
          finalizeLive();
          clearStatus();
          { const { bubble } = addRow("bot"); bubble.textContent = data.content || "Something went wrong on our side. Please try again shortly."; }
          break;
        case "done":
          if (!gotAnswer) {
            const { bubble } = addRow("bot");
            bubble.textContent = "I couldn't find a good answer just yet — try rephrasing your question.";
          }
          finish();
          break;
      }
    };

    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED || !streaming) return;
      if (!gotAnswer) {
        const { bubble } = addRow("bot");
        bubble.textContent = "The connection was interrupted. Please check your network and try again.";
      }
      finish();
    };
  }

  /* ---------- 输入框 ---------- */
  function autoGrow() {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + "px";
  }

  formEl.addEventListener("submit", (e) => { e.preventDefault(); send(inputEl.value); });
  inputEl.addEventListener("input", autoGrow);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(inputEl.value); }
  });
  if (suggestionsEl) {
    suggestionsEl.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (chip) send(chip.textContent);
    });
  }

  inputEl.focus();
})();
