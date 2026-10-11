
document.addEventListener("DOMContentLoaded", () => {
  const searchInput = document.querySelector("#event-search");
  const categoryFilter = document.querySelector("#event-category");
  const eventCards = document.querySelectorAll("[data-event-card]");
  const resultCount = document.querySelector("#event-result-count");
  const emptyMessage = document.querySelector("#event-no-results");

  if (!searchInput || !categoryFilter || eventCards.length === 0) {
    return;
  }

  function filterEvents() {
    const searchText = searchInput.value.trim().toLowerCase();
    const selectedCategory = categoryFilter.value;
    let visibleCount = 0;

    eventCards.forEach((card) => {
      const eventName = card.dataset.name || "";
      const venue = card.dataset.venue || "";
      const category = card.dataset.category || "";

      const matchesSearch =
        eventName.includes(searchText) ||
        venue.includes(searchText);

      const matchesCategory =
        selectedCategory === "all" ||
        category === selectedCategory;

      const visible = matchesSearch && matchesCategory;
      card.hidden = !visible;

      if (visible) visibleCount++;
    });

    if (resultCount) {
      resultCount.textContent =
        `${visibleCount} event${visibleCount === 1 ? "" : "s"} found`;
    }

    if (emptyMessage) {
      emptyMessage.hidden = visibleCount !== 0;
    }
  }

  searchInput.addEventListener("input", filterEvents);
  categoryFilter.addEventListener("change", filterEvents);

  filterEvents();
});







/* Skyline Club: toast notifications */
document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("toast-container");
  if (!container) return;

  function showToast(message, type = "success", duration = 3500) {
    const toast = document.createElement("div");
    toast.className = `app-toast toast-${type}`;

    const icon = document.createElement("span");
    icon.className = "toast-symbol";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent =
      type === "error" ? "!" :
      type === "warning" ? "!" : "✓";

    const text = document.createElement("span");
    text.className = "toast-message";
    text.textContent = message;

    const close = document.createElement("button");
    close.className = "toast-close";
    close.type = "button";
    close.textContent = "×";
    close.setAttribute("aria-label", "Dismiss notification");

    toast.append(icon, text, close);
    container.appendChild(toast);

    requestAnimationFrame(() => {
      toast.classList.add("toast-visible");
    });

    let timer;

    function dismiss() {
      clearTimeout(timer);
      toast.classList.remove("toast-visible");

      toast.addEventListener("transitionend", () => {
        toast.remove();
      }, { once: true });

      // Fallback if the transition does not fire.
      setTimeout(() => toast.remove(), 400);
    }

    close.addEventListener("click", dismiss);

    if (duration > 0) {
      timer = setTimeout(dismiss, duration);
    }

    return toast;
  }

  // Allow other scripts to display a toast.
  window.showToast = showToast;

  // Convert existing Flask flash messages into toast notifications.
  document.querySelectorAll(".page-content .flash").forEach((flash) => {
    const message = flash.textContent
      .replace(/^[✓!]\s*/, "")
      .trim();

    const type = flash.classList.contains("error")
      ? "error"
      : flash.classList.contains("warning")
      ? "warning"
      : "success";

    if (message) showToast(message, type);

    // Keep the original message visible if JavaScript fails later.
    flash.classList.add("flash-toast-converted");
  });
});



/* Skyline Club: persistent light/dark mode */
(() => {
  const root = document.documentElement;
  const storageKey = "skyline-theme";

  function applyTheme(theme) {
    const dark = theme === "dark";

    root.setAttribute("data-theme", dark ? "dark" : "light");

    const icon = document.getElementById("theme-icon");
    const label = document.getElementById("theme-label");
    const button = document.getElementById("theme-toggle");

    if (icon) icon.textContent = dark ? "☀" : "☾";
    if (label) label.textContent = dark ? "Light" : "Dark";

    if (button) {
      button.setAttribute(
        "aria-label",
        dark ? "Switch to light mode" : "Switch to dark mode"
      );
      button.title = dark ? "Switch to light mode" : "Switch to dark mode";
    }
  }

  let savedTheme = "light";

  try {
    savedTheme = localStorage.getItem(storageKey) === "dark"
      ? "dark"
      : "light";
  } catch (error) {
    // The toggle still works if browser storage is unavailable.
  }

  applyTheme(savedTheme);

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    if (!button) return;

    button.addEventListener("click", () => {
      const nextTheme =
        root.getAttribute("data-theme") === "dark" ? "light" : "dark";

      applyTheme(nextTheme);

      try {
        localStorage.setItem(storageKey, nextTheme);
      } catch (error) {
        // Theme changes still apply for the current page.
      }
    });
  });
})();


/* Skyline Club: shop quantity buttons and cart summary */
document.addEventListener("DOMContentLoaded", () => {
  const shopForm = document.getElementById("shop-form");

  // Do nothing on pages other than the shop page.
  if (!shopForm) return;

  const quantityInputs = shopForm.querySelectorAll(".shop-quantity");
  const cartItems = document.getElementById("cart-items");
  const cartEmpty = document.getElementById("cart-empty");
  const cartCount = document.getElementById("cart-count");
  const cartTotal = document.getElementById("cart-total");
  const placeOrderButton = document.getElementById("place-order-btn");
  const cartWarning = document.getElementById("cart-warning");

  function updateCart() {
    let totalItems = 0;
    let subtotal = 0;

    if (cartItems) cartItems.replaceChildren();

    quantityInputs.forEach((input) => {
      const min = Number(input.min || 0);
      const max = input.max === "" ? Infinity : Number(input.max);
      let quantity = Number.parseInt(input.value, 10);

      if (!Number.isFinite(quantity)) quantity = min;

      quantity = Math.max(min, Math.min(max, quantity));
      input.value = quantity;

      if (quantity <= 0) return;

      const price = Number(input.dataset.price) || 0;
      const lineTotal = price * quantity;

      totalItems += quantity;
      subtotal += lineTotal;

      if (cartItems) {
        const line = document.createElement("p");
        const product = input.dataset.product || "Product";
        const size = input.dataset.size || "";
        line.textContent =
          `${product} (${size}) × ${quantity} — ₹${lineTotal.toFixed(2)}`;
        cartItems.appendChild(line);
      }
    });

    if (cartCount) cartCount.textContent = totalItems;
    if (cartTotal) cartTotal.textContent = `₹${subtotal.toFixed(2)}`;

    if (cartEmpty) {
      cartEmpty.hidden = totalItems > 0;
    }

    if (placeOrderButton) {
      placeOrderButton.disabled = totalItems === 0;
    }

    if (cartWarning) {
      cartWarning.textContent = totalItems > 0
        ? "Your selection is ready. Check your details before placing the order."
        : "Select at least one item to continue.";
    }
  }

  // Handle both + and − buttons.
  shopForm.addEventListener("click", (event) => {
    const button = event.target.closest(".qty-btn");
    if (!button || !shopForm.contains(button)) return;

    const inputId = button.dataset.target;
    const input = document.getElementById(inputId);

    if (!input || !shopForm.contains(input)) return;

    const step = Number(button.dataset.step);
    if (step !== 1 && step !== -1) return;

    const min = Number(input.min || 0);
    const max = input.max === "" ? Infinity : Number(input.max);
    const current = Number.parseInt(input.value, 10);
    const quantity = Number.isFinite(current) ? current : min;

    input.value = Math.max(min, Math.min(max, quantity + step));

    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));

    updateCart();
  });

  // Also update the cart when a customer types a quantity manually.
  quantityInputs.forEach((input) => {
    input.addEventListener("input", updateCart);
    input.addEventListener("change", updateCart);
  });

  updateCart();
});

