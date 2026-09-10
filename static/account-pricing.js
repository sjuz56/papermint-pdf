let authMode = 'login';
let currentAccount = null;
let selectedPlan = null;

const authModal = document.getElementById('authModal');
const signInButton = document.getElementById('signInButton');
const authForm = document.getElementById('authForm');
const authStatus = document.getElementById('authStatus');

function setAuthMode(mode) {
  authMode = mode === 'register' ? 'register' : 'login';

  document.querySelectorAll('[data-auth-mode]').forEach(button => {
    button.classList.toggle('active', button.dataset.authMode === authMode);
  });

  const registering = authMode === 'register';
  document.getElementById('authTitle').textContent = registering
    ? 'Create your account'
    : 'Welcome back';
  document.getElementById('authSubtitle').textContent = registering
    ? 'Create an account to manage your subscription.'
    : 'Sign in to manage your PaperMint account.';
  document.getElementById('authSubmit').textContent = registering
    ? 'Create account'
    : 'Sign in';
  document.getElementById('authPassword').autocomplete = registering
    ? 'new-password'
    : 'current-password';
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
    signInButton.textContent = 'Account';
  } else {
    signedOut.classList.remove('hidden');
    signedIn.classList.add('hidden');
    signInButton.textContent = 'Sign in';
    setAuthMode(authMode);
  }
}

function openAuth() {
  authModal.classList.remove('hidden');
  authModal.setAttribute('aria-hidden', 'false');
  renderAccount();

  if (!currentAccount?.authenticated) {
    window.setTimeout(() => document.getElementById('authEmail').focus(), 0);
  }
}

function closeAuth() {
  authModal.classList.add('hidden');
  authModal.setAttribute('aria-hidden', 'true');
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

document.querySelectorAll('[data-auth-mode]').forEach(button => {
  button.addEventListener('click', () => setAuthMode(button.dataset.authMode));
});

authForm?.addEventListener('submit', async event => {
  event.preventDefault();
  authStatus.classList.remove('error');
  authStatus.textContent = authMode === 'register'
    ? 'Creating account…'
    : 'Signing in…';

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
      throw new Error(data.detail || 'Could not sign in.');
    }

    currentAccount = data;
    authForm.reset();
    renderAccount();

    if (selectedPlan) {
      selectedPlan = null;
      closeAuth();
      document.getElementById('pricing')?.scrollIntoView({behavior: 'smooth'});
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

document.querySelectorAll('[data-plan]').forEach(button => {
  button.addEventListener('click', () => {
    selectedPlan = button.dataset.plan;
    if (!currentAccount?.authenticated) {
      setAuthMode('register');
      openAuth();
      return;
    }

    document.getElementById('pricing')?.scrollIntoView({behavior: 'smooth'});
  });
});

authModal?.addEventListener('click', event => {
  if (event.target === authModal) closeAuth();
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !authModal?.classList.contains('hidden')) {
    closeAuth();
  }
});

setAuthMode('login');
loadAccount();
