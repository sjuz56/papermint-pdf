let authMode = 'login';
let currentAccount = null;
let selectedPlan = null;
const accountT = (key, variables) => window.PaperMintI18n?.t(key, variables) || key;

const authModal = document.getElementById('authModal');
const signInButton = document.getElementById('signInButton');
const authForm = document.getElementById('authForm');
const authStatus = document.getElementById('authStatus');
const authPassword = document.getElementById('authPassword');
const authPasswordToggle = document.getElementById('authPasswordToggle');
const checkoutModal = document.getElementById('checkoutModal');
const checkoutConsent = document.getElementById('checkoutConsent');
const checkoutContinue = document.getElementById('checkoutContinue');
const checkoutStatus = document.getElementById('checkoutStatus');

function setPasswordVisibility(visible) {
  if (!authPassword || !authPasswordToggle) return;
  authPassword.type = visible ? 'text' : 'password';
  authPasswordToggle.setAttribute('aria-pressed', String(visible));
  authPasswordToggle.textContent = accountT(visible ? 'auth.hidePassword' : 'auth.showPassword');
  authPasswordToggle.setAttribute(
    'aria-label',
    accountT(visible ? 'auth.hidePassword' : 'auth.showPassword')
  );
}

function setAuthMode(mode) {
  authMode = mode === 'register' ? 'register' : 'login';

  document.querySelectorAll('[data-auth-mode]').forEach(button => {
    button.classList.toggle('active', button.dataset.authMode === authMode);
  });

  const registering = authMode === 'register';
  document.getElementById('authTitle').textContent = registering
    ? accountT('auth.createTitle')
    : accountT('auth.welcome');
  document.getElementById('authSubtitle').textContent = registering
    ? accountT('auth.registerSubtitle')
    : accountT('auth.loginSubtitle');
  document.getElementById('authSubmit').textContent = registering
    ? accountT('auth.createAccount')
    : accountT('auth.signIn');
  document.getElementById('authPassword').autocomplete = registering
    ? 'new-password'
    : 'current-password';
  setPasswordVisibility(false);
  authStatus.textContent = '';
  authStatus.classList.remove('error');
}

function renderAccount() {
  const signedOut = document.getElementById('authSignedOut');
  const signedIn = document.getElementById('authSignedIn');

  if (currentAccount?.authenticated) {
    signedOut.classList.add('hidden');
    signedIn.classList.remove('hidden');
    document.getElementById('accountEmail').textContent = currentAccount.email;
    signInButton.textContent = accountT('auth.account');
    const pro = currentAccount.plan === 'pro';
    const planBadge = document.getElementById('accountPlanBadge');
    planBadge.textContent = pro ? 'PRO' : 'FREE';
    planBadge.classList.toggle('pro', pro);
    document.getElementById('manageSubscriptionButton')?.classList.toggle(
      'hidden',
      !currentAccount.billing_managed
    );
  } else {
    signedOut.classList.remove('hidden');
    signedIn.classList.add('hidden');
    signInButton.textContent = accountT('auth.signIn');
    setAuthMode(authMode);
  }
  window.PaperMintAccount = currentAccount;
  window.dispatchEvent(new CustomEvent('papermint:accountchange', {detail: currentAccount}));
}

function openAuth() {
  authModal.classList.remove('hidden');
  authModal.setAttribute('aria-hidden', 'false');
  renderAccount();

  if (!currentAccount?.authenticated) {
    window.setTimeout(() => document.getElementById('authEmail').focus(), 0);
  }
}

window.openPaperMintAuth = openAuth;

function closeAuth() {
  authModal.classList.add('hidden');
  authModal.setAttribute('aria-hidden', 'true');
}

function showBillingNotice(message) {
  const notice = document.createElement('div');
  notice.className = 'billing-notice';
  notice.setAttribute('role', 'status');
  notice.textContent = message;
  document.body.appendChild(notice);
  window.setTimeout(() => notice.remove(), 7000);
}

function updateCheckoutPlanText() {
  const planText = document.getElementById('checkoutPlanText');
  if (!planText || !selectedPlan) return;
  planText.textContent = selectedPlan === 'yearly'
    ? accountT('billing.yearlySummary')
    : accountT('billing.monthlySummary');
}

function openCheckoutConsent(plan) {
  selectedPlan = plan === 'yearly' ? 'yearly' : 'monthly';
  checkoutConsent.checked = false;
  checkoutContinue.disabled = true;
  checkoutStatus.textContent = '';
  checkoutStatus.classList.remove('error');
  updateCheckoutPlanText();
  checkoutModal.classList.remove('hidden');
  checkoutModal.setAttribute('aria-hidden', 'false');
}

function closeCheckout() {
  checkoutModal.classList.add('hidden');
  checkoutModal.setAttribute('aria-hidden', 'true');
  selectedPlan = null;
}

function safeStripeRedirect(rawUrl, expectedHost) {
  const target = new URL(rawUrl);
  if (target.protocol !== 'https:' || target.hostname !== expectedHost) {
    throw new Error(accountT('billing.failed'));
  }
  window.location.assign(target.href);
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_) {
    return {};
  }
}

async function loadAccount() {
  try {
    const response = await fetch('/api/auth/me', {cache: 'no-store'});
    currentAccount = response.ok
      ? await readJson(response)
      : {authenticated: false};
  } catch (_) {
    currentAccount = {authenticated: false};
  }
  renderAccount();
}

signInButton?.addEventListener('click', openAuth);
document.getElementById('authClose')?.addEventListener('click', closeAuth);
authPasswordToggle?.addEventListener('click', () => {
  setPasswordVisibility(authPassword?.type === 'password');
  authPassword?.focus();
});

document.querySelectorAll('[data-auth-mode]').forEach(button => {
  button.addEventListener('click', () => setAuthMode(button.dataset.authMode));
});

authForm?.addEventListener('submit', async event => {
  event.preventDefault();
  authStatus.classList.remove('error');
  authStatus.textContent = authMode === 'register'
    ? accountT('auth.creating')
    : accountT('auth.signingIn');

  const fields = new FormData(authForm);
  const endpoint = authMode === 'register'
    ? '/api/auth/register'
    : '/api/auth/login';

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        email: fields.get('email'),
        password: fields.get('password')
      })
    });
    const data = await readJson(response);

    if (!response.ok) {
      throw new Error(data.detail || accountT('auth.failed'));
    }

    currentAccount = data;
    authForm.reset();
    setPasswordVisibility(false);
    renderAccount();

    if (selectedPlan) {
      const plan = selectedPlan;
      closeAuth();
      openCheckoutConsent(plan);
    }
  } catch (error) {
    authStatus.textContent = error.message;
    authStatus.classList.add('error');
  }
});

document.getElementById('logoutButton')?.addEventListener('click', async () => {
  try {
    await fetch('/api/auth/logout', {method: 'POST'});
  } finally {
    currentAccount = {authenticated: false};
    closeAuth();
    renderAccount();
  }
});

document.getElementById('manageSubscriptionButton')?.addEventListener('click', async () => {
  try {
    const response = await fetch('/api/billing/portal', {method: 'POST'});
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.detail || accountT('billing.portalFailed'));
    safeStripeRedirect(data.url, 'billing.stripe.com');
  } catch (error) {
    showBillingNotice(error.message);
  }
});

document.querySelectorAll('[data-plan]').forEach(button => {
  button.addEventListener('click', () => {
    selectedPlan = button.dataset.plan;

    if (selectedPlan === 'free') {
      selectedPlan = null;
      document.getElementById('tools')?.scrollIntoView({behavior: 'smooth'});
      return;
    }

    if (!currentAccount?.authenticated) {
      setAuthMode('register');
      openAuth();
      return;
    }
    openCheckoutConsent(selectedPlan);
  });
});

checkoutConsent?.addEventListener('change', () => {
  checkoutContinue.disabled = !checkoutConsent.checked;
});

document.getElementById('checkoutClose')?.addEventListener('click', closeCheckout);
checkoutModal?.addEventListener('click', event => {
  if (event.target === checkoutModal) closeCheckout();
});

checkoutContinue?.addEventListener('click', async () => {
  if (!checkoutConsent.checked || !selectedPlan) return;
  checkoutContinue.disabled = true;
  checkoutStatus.classList.remove('error');
  checkoutStatus.textContent = accountT('billing.opening');
  try {
    const response = await fetch('/api/billing/checkout', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({plan: selectedPlan, accepted_terms: checkoutConsent.checked})
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.detail || accountT('billing.failed'));
    safeStripeRedirect(data.url, 'checkout.stripe.com');
  } catch (error) {
    checkoutStatus.textContent = error.message;
    checkoutStatus.classList.add('error');
    checkoutContinue.disabled = !checkoutConsent.checked;
  }
});

authModal?.addEventListener('click', event => {
  if (event.target === authModal) closeAuth();
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !authModal?.classList.contains('hidden')) {
    closeAuth();
  }
  if (event.key === 'Escape' && !checkoutModal?.classList.contains('hidden')) {
    closeCheckout();
  }
});

setAuthMode('login');
loadAccount();

window.addEventListener('papermint:languagechange', () => {
  renderAccount();
  setPasswordVisibility(authPassword?.type === 'text');
  updateCheckoutPlanText();
});

setPasswordVisibility(false);

const checkoutResult = new URLSearchParams(window.location.search).get('checkout');
if (checkoutResult === 'success') {
  showBillingNotice(accountT('billing.success'));
  window.setTimeout(loadAccount, 1800);
} else if (checkoutResult === 'cancelled') {
  showBillingNotice(accountT('billing.cancelled'));
}
