let authMode = 'login';
let currentAccount = null;
let selectedPlan = null;
const accountT = (key, variables) => window.PaperMintI18n?.t(key, variables) || key;

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
  } else {
    signedOut.classList.remove('hidden');
    signedIn.classList.add('hidden');
    signInButton.textContent = accountT('auth.signIn');
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

window.addEventListener('papermint:languagechange', () => {
  renderAccount();
});
