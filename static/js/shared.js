// ═══════════════════════════════════════
// CLOTHYK — Shared JS
// ═══════════════════════════════════════

// ─── Toast Notifications ──────────────────────────────────────────────────
function showToast(message, type = "success") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; toast.style.transition = "opacity .3s"; }, 2700);
  setTimeout(() => toast.remove(), 3000);
}

// ─── API Helper ───────────────────────────────────────────────────────────
async function apiFetch(url, options = {}) {
  try {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...options.headers },
      ...options
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Request failed" }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (e) {
    throw e;
  }
}

// ─── Cart ─────────────────────────────────────────────────────────────────
const Cart = {
  get() { return JSON.parse(localStorage.getItem("clothyk_cart") || "[]"); },
  save(items) { localStorage.setItem("clothyk_cart", JSON.stringify(items)); updateCartBadge(); },
  add(product, size, color) {
    const items = this.get();
    const existing = items.find(i => i.id === product.id && i.size === size && i.color === color);
    if (existing) {
      existing.qty = (existing.qty || 1) + 1;
    } else {
      items.push({ id: product.id, name: product.name, price: product.our_price, image: product.image, size, color, qty: 1 });
    }
    this.save(items);
    showToast("Added to cart! 🛍️");
  },
  remove(id, size, color) {
    const items = this.get().filter(i => !(i.id === id && i.size === size && i.color === color));
    this.save(items);
  },
  count() { return this.get().reduce((s, i) => s + (i.qty || 1), 0); },
  total() { return this.get().reduce((s, i) => s + i.price * (i.qty || 1), 0); },
  clear() { localStorage.removeItem("clothyk_cart"); updateCartBadge(); }
};

// ─── Wishlist ─────────────────────────────────────────────────────────────
const Wishlist = {
  get() { return JSON.parse(localStorage.getItem("clothyk_wishlist") || "[]"); },
  save(items) { localStorage.setItem("clothyk_wishlist", JSON.stringify(items)); updateWishlistBadge(); },
  toggle(product) {
    const items = this.get();
    const idx = items.findIndex(i => i.id === product.id);
    if (idx > -1) {
      items.splice(idx, 1);
      showToast("Removed from wishlist", "warning");
    } else {
      items.push({ id: product.id, name: product.name, price: product.our_price, image: product.image, code: product.shopkeeper_code });
      showToast("Added to wishlist ❤️");
    }
    this.save(items);
    return idx === -1; // true = added
  },
  has(id) { return this.get().some(i => i.id === id); },
  count() { return this.get().length; }
};

function updateCartBadge() {
  const badge = document.getElementById("cart-badge");
  if (badge) {
    const count = Cart.count();
    badge.textContent = count;
    badge.style.display = count > 0 ? "flex" : "none";
  }
}
function updateWishlistBadge() {
  const badge = document.getElementById("wishlist-badge");
  if (badge) {
    const count = Wishlist.count();
    badge.textContent = count;
    badge.style.display = count > 0 ? "flex" : "none";
  }
}

// ─── Skeleton Helpers ─────────────────────────────────────────────────────
function skeletonCards(container, count = 6) {
  container.innerHTML = Array(count).fill(`
    <div class="product-card">
      <div class="skeleton skeleton-img"></div>
      <div class="card-body">
        <div class="skeleton skeleton-text" style="width:70%"></div>
        <div class="skeleton skeleton-text" style="width:40%"></div>
      </div>
    </div>
  `).join("");
}

// ─── Format price ─────────────────────────────────────────────────────────
function formatPrice(n) { return "₹" + Number(n).toLocaleString("en-IN"); }

// ─── Render product card ──────────────────────────────────────────────────
function renderProductCard(p, onclick) {
  const wishlisted = Wishlist.has(p.id);
  return `
    <div class="product-card" onclick="${onclick || `window.location='/product/${p.id}'`}">
      <img src="${p.image || '/static/images/placeholder.svg'}" alt="${p.name}" loading="lazy">
      <button class="wishlist-btn ${wishlisted ? 'active' : ''}" onclick="event.stopPropagation(); toggleWishlist(this, ${JSON.stringify(p).replace(/"/g,'&quot;')})" title="Wishlist">
        ${wishlisted ? '❤️' : '🤍'}
      </button>
      <div class="product-card-body">
        <div class="product-card-name">${p.name}</div>
        <div class="product-card-price">${formatPrice(p.our_price)}</div>
        <div class="product-card-code">Code: ${p.shopkeeper_code || ''}</div>
      </div>
    </div>
  `;
}

function toggleWishlist(btn, product) {
  const added = Wishlist.toggle(product);
  btn.textContent = added ? '❤️' : '🤍';
  btn.classList.toggle('active', added);
}

// ─── Init on load ─────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  updateCartBadge();
  updateWishlistBadge();
});
