(function () {
  "use strict";
  const usersEl = document.getElementById("users");
  const errorEl = document.getElementById("user-error");
  const kbStatusEl = document.getElementById("kb-status");
  const passwordDialog = document.getElementById("password-dialog");
  const passwordForm = document.getElementById("reset-password");
  const passwordInput = document.getElementById("reset-password-value");
  const passwordError = document.getElementById("password-error");
  let passwordTargetUserId = null;

  async function api(path, options) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...(options || {}),
    });
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Authentication required.");
    }
    if (response.status === 403) {
      window.location.href = "/";
      throw new Error("Administrator access required.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Request failed.");
    return data;
  }

  function actionButton(text, className, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    if (className) button.className = className;
    button.addEventListener("click", handler);
    return button;
  }

  async function loadUsers() {
    const data = await api("/api/admin/users");
    usersEl.replaceChildren();
    for (const user of data.users) {
      const row = document.createElement("div");
      row.className = "user-row";

      const identity = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = user.username;
      const id = document.createElement("div");
      id.className = "muted";
      id.textContent = `Business ID: ${user.business_id}`;
      identity.append(name, id);

      const role = document.createElement("select");
      for (const value of ["user", "admin"]) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        option.selected = value === user.role;
        role.appendChild(option);
      }
      role.addEventListener("change", async () => {
        try {
          await api(`/api/admin/users/${encodeURIComponent(user.id)}`, {
            method: "PATCH",
            body: JSON.stringify({ role: role.value }),
          });
        } catch (error) {
          errorEl.textContent = error.message;
          role.value = user.role;
        }
      });

      const active = document.createElement("span");
      active.textContent = user.is_active ? "Active" : "Disabled";
      active.className = "muted";

      const actions = document.createElement("div");
      actions.className = "row-actions";
      actions.append(
        actionButton(user.is_active ? "Disable" : "Enable", "secondary", async () => {
          try {
            await api(`/api/admin/users/${encodeURIComponent(user.id)}`, {
              method: "PATCH",
              body: JSON.stringify({ is_active: !user.is_active }),
            });
            await loadUsers();
          } catch (error) { errorEl.textContent = error.message; }
        }),
        actionButton("Reset password", "secondary", () => {
          passwordTargetUserId = user.id;
          passwordInput.value = "";
          passwordError.textContent = "";
          passwordDialog.showModal();
          passwordInput.focus();
        }),
        actionButton("Clear memory", "danger", async () => {
          if (!window.confirm(`Clear long-term memory for ${user.username}?`)) return;
          try {
            const result = await api(
              `/api/admin/users/${encodeURIComponent(user.id)}/memories`,
              { method: "DELETE" },
            );
            window.alert(`Deleted ${result.deleted} memories.`);
          } catch (error) { errorEl.textContent = error.message; }
        }),
      );
      row.append(identity, role, active, actions);
      usersEl.appendChild(row);
    }
  }

  async function loadKnowledgeStatus() {
    const status = await api("/api/admin/knowledge/status");
    const result = status.last_result;
    kbStatusEl.textContent = status.running
      ? "Scan running…"
      : result
        ? `Last scan: ${result.status}; added ${result.added || 0}, updated ${result.updated || 0}, deleted ${result.deleted || 0}, failed ${result.failed || 0}.`
        : `Automatic scan is ${status.enabled ? "enabled" : "disabled"} (${status.interval_seconds}s interval).`;
  }

  document.getElementById("create-user").addEventListener("submit", async (event) => {
    event.preventDefault();
    errorEl.textContent = "";
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username: document.getElementById("new-username").value,
          business_id: document.getElementById("new-business-id").value || null,
          password: document.getElementById("new-password").value,
          role: document.getElementById("new-role").value,
        }),
      });
      event.target.reset();
      await loadUsers();
    } catch (error) { errorEl.textContent = error.message; }
  });

  document.getElementById("refresh-users").addEventListener("click", () => {
    loadUsers().catch((error) => { errorEl.textContent = error.message; });
  });
  document.getElementById("scan-kb").addEventListener("click", async () => {
    try {
      await api("/api/admin/knowledge/scan", { method: "POST" });
      kbStatusEl.textContent = "Scan started…";
      window.setTimeout(() => loadKnowledgeStatus().catch(() => {}), 1500);
    } catch (error) { kbStatusEl.textContent = error.message; }
  });
  document.getElementById("cancel-password-reset").addEventListener("click", () => {
    passwordDialog.close();
  });
  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!passwordTargetUserId) return;
    passwordError.textContent = "";
    try {
      await api(`/api/admin/users/${encodeURIComponent(passwordTargetUserId)}`, {
        method: "PATCH",
        body: JSON.stringify({ password: passwordInput.value }),
      });
      passwordDialog.close();
      passwordTargetUserId = null;
    } catch (error) {
      passwordError.textContent = error.message;
    }
  });

  loadUsers().catch((error) => { errorEl.textContent = error.message; });
  loadKnowledgeStatus().catch((error) => { kbStatusEl.textContent = error.message; });
})();
