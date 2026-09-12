        let isFirstRun = false;
        let setupMode = false;

        // Theme management
        const themeToggle = document.getElementById('themeToggle');
        const themeIcon = document.getElementById('themeIcon');

        // Load saved theme or default to light mode
        const savedTheme = localStorage.getItem('theme') || 'light';
        if (savedTheme === 'dark') {
            document.body.classList.add('dark-mode');
            themeIcon.className = 'fas fa-sun';
        }

        themeToggle.addEventListener('click', () => {
            document.body.classList.toggle('dark-mode');
            const isDark = document.body.classList.contains('dark-mode');
            themeIcon.className = isDark ? 'fas fa-sun' : 'fas fa-moon';
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
        });

        // Check authentication status on load
        window.addEventListener('DOMContentLoaded', async () => {
            try {
                const response = await fetch('/api/auth/status');
                const data = await response.json();

                if (data.authenticated) {
                    // Already logged in, redirect to main page
                    window.location.href = '/';
                    return;
                }

                if (data.first_run) {
                    // First run - setup mode
                    isFirstRun = true;
                    setupMode = true;
                    document.getElementById('firstRunNotice').style.display = 'block';
                    document.getElementById('usernameGroup').style.display = 'none';
                    document.getElementById('rememberGroup').style.display = 'none';
                    document.getElementById('passwordRequirements').style.display = 'block';
                    document.getElementById('confirmPasswordGroup').style.display = 'block';
                    document.getElementById('confirmPassword').setAttribute('required', 'required');
                    document.getElementById('btnText').textContent = 'Create Admin Account';
                    document.getElementById('username').removeAttribute('required');
                    document.getElementById('password').setAttribute('autocomplete', 'new-password');
                }
            } catch (error) {
                console.error('Failed to check auth status:', error);
            }
        });

        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();

            const errorDiv = document.getElementById('errorMessage');
            const successDiv = document.getElementById('successMessage');
            const submitBtn = document.getElementById('submitBtn');
            const btnText = document.getElementById('btnText');
            const spinner = document.getElementById('loadingSpinner');

            // Hide messages
            errorDiv.style.display = 'none';
            successDiv.style.display = 'none';

            // Show loading
            submitBtn.disabled = true;
            btnText.style.display = 'none';
            spinner.style.display = 'block';

            const password = document.getElementById('password').value;
            const confirmPassword = document.getElementById('confirmPassword').value;

            // Check password match for first run
            if (setupMode && password !== confirmPassword) {
                errorDiv.textContent = 'Passwords do not match';
                errorDiv.style.display = 'block';
                submitBtn.disabled = false;
                btnText.style.display = 'inline';
                spinner.style.display = 'none';
                return;
            }

            try {
                let response;

                if (isFirstRun && setupMode) {
                    // First run setup
                    response = await fetch('/api/auth/setup', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ password })
                    });
                } else {
                    // Normal login
                    const username = document.getElementById('username').value || 'admin';
                    const remember = document.getElementById('remember').checked;

                    response = await fetch('/api/auth/login', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ username, password, remember })
                    });
                }

                const data = await response.json();

                if (response.ok) {
                    successDiv.textContent = data.message || 'Login successful!';
                    successDiv.style.display = 'block';

                    // Redirect to main page after a short delay
                    setTimeout(() => {
                        window.location.href = '/';
                    }, 500);
                } else {
                    // Check for first-time setup requirement
                    if (data.first_setup) {
                        setupMode = true;
                        document.getElementById('firstRunNotice').style.display = 'block';
                        document.getElementById('firstRunNotice').innerHTML =
                            '<strong>First-Time Setup</strong><br>' +
                            'The admin account exists but needs a password. ' +
                            'Please enter a new password (minimum 8 characters).';
                        document.getElementById('passwordRequirements').style.display = 'block';
                        document.getElementById('confirmPasswordGroup').style.display = 'block';
                        document.getElementById('confirmPassword').setAttribute('required', 'required');
                        document.getElementById('btnText').textContent = 'Set Password';
                        document.getElementById('password').value = '';
                        document.getElementById('confirmPassword').value = '';
                        document.getElementById('password').setAttribute('autocomplete', 'new-password');
                        document.getElementById('password').focus();
                    } else {
                        errorDiv.textContent = data.error || 'Login failed';
                        errorDiv.style.display = 'block';
                    }
                }
            } catch (error) {
                errorDiv.textContent = 'Connection failed. Please try again.';
                errorDiv.style.display = 'block';
                console.error('Login error:', error);
            } finally {
                // Reset button
                submitBtn.disabled = false;
                btnText.style.display = 'inline';
                spinner.style.display = 'none';
            }
        });

        // Auto-focus username or password field
        window.addEventListener('load', () => {
            if (isFirstRun || setupMode) {
                document.getElementById('password').focus();
            } else {
                document.getElementById('username').focus();
            }
        });
