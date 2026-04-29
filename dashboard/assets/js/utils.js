// UI Utility Helpers
const showToast = (message, type = 'info') => {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    const icon = type === 'success' ? '✅' : (type === 'error' ? '❌' : 'ℹ️');
    
    toast.innerHTML = `<span>${icon}</span><span class="text-sm font-medium">${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s ease-out forwards';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
};

const getCookie = (name) => {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
};

const toggleTheme = () => {
    const html = document.documentElement;
    const isDark = html.classList.contains('dark');
    const lightIcon = document.getElementById('theme-icon-light');
    const darkIcon = document.getElementById('theme-icon-dark');
    
    if (isDark) {
        html.classList.remove('dark');
        localStorage.setItem('theme', 'light');
        if(lightIcon) lightIcon.classList.remove('hidden');
        if(darkIcon) darkIcon.classList.add('hidden');
    } else {
        html.classList.add('dark');
        localStorage.setItem('theme', 'dark');
        if(darkIcon) darkIcon.classList.remove('hidden');
        if(lightIcon) lightIcon.classList.add('hidden');
    }
};

const initThemeUI = () => {
     const storedTheme = localStorage.getItem('theme');
     const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
     const shouldBeDark = storedTheme === 'dark' || (!storedTheme && prefersDark);
     
     if (!shouldBeDark) {
        document.documentElement.classList.remove('dark');
        if(document.getElementById('theme-icon-light')) document.getElementById('theme-icon-light').classList.remove('hidden');
        if(document.getElementById('theme-icon-dark')) document.getElementById('theme-icon-dark').classList.add('hidden');
     } else {
        document.documentElement.classList.add('dark');
        if(document.getElementById('theme-icon-dark')) document.getElementById('theme-icon-dark').classList.remove('hidden');
        if(document.getElementById('theme-icon-light')) document.getElementById('theme-icon-light').classList.add('hidden');
     }
};
