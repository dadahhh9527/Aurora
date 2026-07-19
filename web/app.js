(function () {
  "use strict";

  const messagesEl = document.getElementById("messages");
  const welcomeEl = document.getElementById("welcome");
  const formEl = document.getElementById("chat-form");
  const inputEl = document.getElementById("input");
  const sendEl = document.getElementById("send");
  const suggestionsEl = document.getElementById("suggestions");
  const currentUserEl = document.getElementById("current-user");
  const adminLinkEl = document.getElementById("admin-link");
  const logoutEl = document.getElementById("logout");
  const newChatEl = document.getElementById("new-chat");

  let streaming = false;
  let currentController = null;

  function newConversationId() {
    if (window.crypto && crypto.randomUUID) return "c-" + crypto.randomUUID();
    return "c-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
  }
  let conversationId = newConversationId();

  const BOT_AVATAR =
    '<svg viewBox="0 0 32 32" width="18" height="18">' +
    '<path d="M8 21c3.5-7 12.5-7 16 0" stroke="#fff" stroke-width="2.6" fill="none" stroke-linecap="round"></path>' +
    '<circle cx="16" cy="12" r="2.6" fill="#fff"></circle></svg>';

  /* ---------- Safe Markdown rendering: escape first, then apply limited formatting ---------- */
  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderMarkdown(src) {
    const codeBlocks = [];
    // Extract code blocks so their content is not processed twice.
    let text = src.replace(/```([\s\S]*?)```/g, (_, code) => {
      codeBlocks.push(code.replace(/^\n+|\n+$/g, ""));
      return "\u0000CODE" + (codeBlocks.length - 1) + "\u0000";
    });

    text = escapeHtml(text);

    // Inline code, bold, and italic text.
    text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

    // Links are restricted to HTTP and HTTPS.
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

    // Process lists and paragraphs line by line.
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

    // Restore escaped code blocks.
    html = html.replace(/\u0000CODE(\d+)\u0000/g, (_, i) =>
      "<pre><code>" + escapeHtml(codeBlocks[+i]) + "</code></pre>");

    return html;
  }

  /* ---------- Basic DOM helpers ---------- */
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

  /* Status line for model work and tool calls. */
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

  /* ---------- Streaming render state ---------- */
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
    // Stream plain text with a cursor, then render Markdown when complete.
    live.bubble.textContent = live.raw;
    scrollToBottom();
  }

  function onTool(mid, label) {
    // A tool call marks this message as internal work; replace it with tool status.
    if (live && live.mid === mid) {
      live.hadTool = true;
      if (live.row && live.row.parentNode) live.row.remove();
      live = null;
    }
    showStatus((label || "Working") + "…", false);
  }

  /* ---------- Request and response handling ---------- */
  function setBusy(busy) {
    streaming = busy;
    sendEl.disabled = busy;
    inputEl.disabled = busy;
    if (!busy) { inputEl.focus(); autoGrow(); }
  }

  async function send(text) {
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
    const controller = new AbortController();
    currentController = controller;
    const finish = () => {
      if (currentController === controller) currentController = null;
      finalizeLive();
      clearStatus();
      setBusy(false);
    };

    const handleEvent = (eventText) => {
      const dataLine = eventText.split("\n").find((line) => line.startsWith("data:"));
      if (!dataLine) return false;
      let data;
      try { data = JSON.parse(dataLine.slice(5).trim()); } catch (_) { return false; }

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
          return true;
      }
      return false;
    };

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        signal: controller.signal,
        body: JSON.stringify({ message: query, conversation_id: conversationId }),
      });
      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "Request failed.");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      let doneEvent = false;
      while (!doneEvent) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        const frames = pending.split("\n\n");
        pending = frames.pop() || "";
        for (const frame of frames) {
          if (handleEvent(frame)) {
            doneEvent = true;
            break;
          }
        }
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      if (!gotAnswer) {
        const { bubble } = addRow("bot");
        bubble.textContent = error.message || "The connection was interrupted. Please try again.";
      }
    } finally {
      finish();
    }
  }

  /* ---------- Composer ---------- */
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

  async function loadIdentity() {
    const response = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (response.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (!response.ok) return;
    const data = await response.json();
    if (currentUserEl) currentUserEl.textContent = data.user.username;
    if (adminLinkEl && data.user.role === "admin") adminLinkEl.hidden = false;
  }

  if (logoutEl) {
    logoutEl.addEventListener("click", async () => {
      if (currentController) currentController.abort();
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
      });
      window.location.href = "/login";
    });
  }

  if (newChatEl) {
    newChatEl.addEventListener("click", async () => {
      if (currentController) currentController.abort();
      await fetch("/api/conversations/current", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ conversation_id: conversationId }),
      }).catch(() => {});
      conversationId = newConversationId();
      window.location.reload();
    });
  }

  loadIdentity().catch(() => {});
  inputEl.focus();
})();
