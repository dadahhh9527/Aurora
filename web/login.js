(function () {
  "use strict";
  const form = document.getElementById("login-form");
  const errorEl = document.getElementById("error");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorEl.textContent = "";
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          username: document.getElementById("username").value,
          password: document.getElementById("password").value,
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Unable to sign in.");
      }
      window.location.href = "/";
    } catch (error) {
      errorEl.textContent = error.message;
      button.disabled = false;
    }
  });
})();
