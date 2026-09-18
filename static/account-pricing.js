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
const authMainPanel = document.getElementById('authMainPanel');
const passwordResetPanel = document.getElementById('passwordResetPanel');
const passwordResetStatus = document.getElementById('passwordResetStatus');
const passwordResetRequestForm = document.getElementById('passwordResetRequestForm');
const passwordResetConfirmForm = document.getElementById('passwordResetConfirmForm');
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

function setPasswordResetMode(confirming = false) {
  authMainPanel?.classList.add('hidden');
  passwordResetPanel?.classList.remove('hidden');
  passwordResetRequestForm?.classList.toggle('hidden', confirming);
  passwordResetConfirmForm?.classList.toggle('hidden', !confirming);
  const title = document.getElementById('passwordResetTitle');
  const subtitle = document.getElementById('passwordResetSubtitle');
  if (title) title.textContent = confirming ? 'Choose a new password' : 'Reset password';
  if (subtitle) {
    subtitle.textContent = confirming
      ? 'Enter a new password for your PDFaspect account.'
      : 'Enter your email and we’ll send you a secure reset link.';
  }
  if (passwordResetStatus) {
    passwordResetStatus.textContent = '';
    passwordResetStatus.classList.remove('error');
  }
  window.setTimeout(() => {
    const target = confirming
      ? document.getElementById('passwordResetNewPassword')
      : document.getElementById('passwordResetEmail');
    target?.focus();
  }, 0);
}

function leavePasswordResetMode() {
  passwordResetPanel?.classList.add('hidden');
  authMainPanel?.classList.remove('hidden');
  passwordResetRequestForm?.reset();
  passwordResetConfirmForm?.reset();
  if (passwordResetStatus) {
    passwordResetStatus.textContent = '';
    passwordResetStatus.classList.remove('error');
  }
  setAuthMode('login');
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
    const periodEnd = currentAccount.subscription_period_end;
    const subscriptionStatus = document.getElementById('subscriptionStatusText');
    if (subscriptionStatus) {
      const hasDate = Number.isFinite(Number(periodEnd)) && Number(periodEnd) > 0;
      if (pro && hasDate) {
        const date = new Intl.DateTimeFormat(document.documentElement.lang || 'en', {
          year: 'numeric', month: 'long', day: 'numeric'
        }).format(new Date(Number(periodEnd) * 1000));
        subscriptionStatus.textContent = currentAccount.subscription_cancel_at_period_end
          ? accountT('billing.endsOn', {date})
          : accountT('billing.renewsOn', {date});
        subscriptionStatus.classList.remove('hidden');
      } else {
        subscriptionStatus.textContent = '';
        subscriptionStatus.classList.add('hidden');
      }
    }
    document.getElementById('manageSubscriptionButton')?.classList.toggle(
      'hidden',
      !currentAccount.billing_managed
    );
    document.getElementById('cancelSubscriptionButton')?.classList.toggle(
      'hidden',
      !pro || !currentAccount.billing_managed || currentAccount.subscription_cancel_at_period_end
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
  button.addEventListener('click', () => {
    passwordResetPanel?.classList.add('hidden');
    authMainPanel?.classList.remove('hidden');
    setAuthMode(button.dataset.authMode);
  });
});

document.getElementById('forgotPasswordButton')?.addEventListener('click', () => {
  const email = document.getElementById('authEmail')?.value || '';
  const resetEmail = document.getElementById('passwordResetEmail');
  if (resetEmail) resetEmail.value = email;
  setPasswordResetMode(false);
});

document.getElementById('passwordResetBack')?.addEventListener('click', leavePasswordResetMode);

passwordResetRequestForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.getElementById('passwordResetRequestSubmit');
  button.disabled = true;
  passwordResetStatus.classList.remove('error');
  passwordResetStatus.textContent = 'Sending reset link…';

  try {
    const fields = new FormData(passwordResetRequestForm);
    const response = await fetch('/api/auth/password-reset/request', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: fields.get('email')})
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.detail || 'Could not send reset link.');
    passwordResetStatus.textContent =
      data.message || 'If an account exists for that email, a reset link has been sent.';
  } catch (error) {
    passwordResetStatus.textContent = error.message;
    passwordResetStatus.classList.add('error');
  } finally {
    button.disabled = false;
  }
});

passwordResetConfirmForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const token = new URLSearchParams(window.location.search).get('reset_token');
  const button = document.getElementById('passwordResetConfirmSubmit');
  button.disabled = true;
  passwordResetStatus.classList.remove('error');
  passwordResetStatus.textContent = 'Changing password…';

  try {
    if (!token) throw new Error('This password reset link is invalid or has expired.');
    const fields = new FormData(passwordResetConfirmForm);
    const response = await fetch('/api/auth/password-reset/confirm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token, password: fields.get('password')})
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.detail || 'Could not change password.');
    passwordResetStatus.textContent = data.message || 'Password changed. You can sign in now.';
    window.history.replaceState({}, '', window.location.pathname + '#account');
    window.setTimeout(leavePasswordResetMode, 1200);
  } catch (error) {
    passwordResetStatus.textContent = error.message;
    passwordResetStatus.classList.add('error');
  } finally {
    button.disabled = false;
  }
});

authForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const completedMode = authMode;
  const submitButton = document.getElementById('authSubmit');
  submitButton.disabled = true;
  authStatus.classList.remove('error');
  authStatus.textContent = completedMode === 'register'
    ? accountT('auth.creating')
    : accountT('auth.signingIn');

  const fields = new FormData(authForm);
  const endpoint = completedMode === 'register'
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
    showBillingNotice(`${accountT('auth.signedAs')} ${data.email}.`);

    if (selectedPlan) {
      const plan = selectedPlan;
      closeAuth();
      openCheckoutConsent(plan);
    } else {
      closeAuth();
    }
  } catch (error) {
    authStatus.textContent = error.message;
    authStatus.classList.add('error');
  } finally {
    submitButton.disabled = false;
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

document.getElementById('cancelSubscriptionButton')?.addEventListener('click', async event => {
  if (!window.confirm(accountT('billing.cancelConfirm'))) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch('/api/billing/cancel', {method: 'POST'});
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.detail || accountT('billing.cancelFailed'));
    currentAccount.subscription_cancel_at_period_end = true;
    currentAccount.subscription_period_end = data.current_period_end;
    renderAccount();
    showBillingNotice(accountT('billing.cancelScheduled'));
  } catch (error) {
    showBillingNotice(error.message);
  } finally {
    button.disabled = false;
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

const passwordResetToken = new URLSearchParams(window.location.search).get('reset_token');
if (passwordResetToken) {
  openAuth();
  setPasswordResetMode(true);
}

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
