/**
 * Authentication and User Management Module for PixelProbe
 */

const AuthManager = {
    currentUser: null,

    /**
     * Initialize the authentication manager
     */
    async init() {
        await this.checkAuthStatus();
        this.setupEventListeners();
        this.updateUI();
    },

    /**
     * Check current authentication status
     */
    async checkAuthStatus() {
        try {
            const response = await fetch('/api/auth/status');
            const data = await response.json();

            if (!data.authenticated) {
                // Redirect to login if not authenticated
                window.location.href = '/login';
                return;
            }

            this.currentUser = data.user;
            this.updateUserDisplay();
        } catch (error) {
            window.location.href = '/login';
        }
    },

    /**
     * Update UI elements based on authentication state
     */
    updateUI() {
        // Update user info in the UI
        this.updateUserDisplay();

        // Show/hide admin features
        if (this.currentUser && this.currentUser.is_admin) {
            document.querySelectorAll('.admin-only').forEach(el => {
                el.style.display = '';
            });
        }
    },

    /**
     * Update user display in the UI
     */
    updateUserDisplay() {
        const userDisplay = document.getElementById('currentUserDisplay');
        if (userDisplay && this.currentUser) {
            userDisplay.textContent = this.currentUser.username;
        }
    },

    /**
     * Setup event listeners
     */
    setupEventListeners() {
        // Logout button
        const logoutBtn = document.getElementById('logoutBtn');
        if (logoutBtn) {
            logoutBtn.addEventListener('click', () => this.logout());
        }

        // User management buttons
        const userManagementBtn = document.getElementById('userManagementBtn');
        if (userManagementBtn) {
            userManagementBtn.addEventListener('click', () => this.showUserManagement());
        }

        const apiTokensBtn = document.getElementById('apiTokensBtn');
        if (apiTokensBtn) {
            apiTokensBtn.addEventListener('click', () => this.showApiTokens());
        }

        const changePasswordBtn = document.getElementById('changePasswordBtn');
        if (changePasswordBtn) {
            changePasswordBtn.addEventListener('click', () => this.showChangePassword());
        }
    },

    /**
     * Logout the current user
     */
    async logout() {
        try {
            const response = await fetch('/api/auth/logout', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Accept': 'application/json'
                }
            });

            // Always redirect to login, even if the logout fails
            // (user might already be logged out)
            window.location.href = '/login';
        } catch (error) {
            // Redirect anyway - connection issues shouldn't prevent logout
            window.location.href = '/login';
        }
    },

    /**
     * Show user management modal
     */
    async showUserManagement() {
        const modal = document.getElementById('userManagementModal');
        if (!modal) {
            this.createUserManagementModal();
        }

        await this.loadUsers();
        document.getElementById('userManagementModal').style.display = 'block';
    },

    /**
     * Load users list
     */
    async loadUsers() {
        try {
            const response = await fetch('/api/users');
            const data = await response.json();

            const usersList = document.getElementById('usersList');
            usersList.innerHTML = '';

            data.users.forEach(user => {
                const userItem = document.createElement('div');
                userItem.className = 'exclusion-item';
                userItem.innerHTML = `
                    <span>
                        <strong>${user.username}</strong> - ${user.email}
                        ${user.is_admin ? ' <span class="badge">ADMIN</span>' : ''}
                    </span>
                    ${user.id !== this.currentUser.id ?
                        `<button class="btn btn-sm btn-danger" onclick="AuthManager.deleteUser(${user.id})">
                            <i class="fas fa-trash"></i>
                        </button>` :
                        ''}
                `;
                usersList.appendChild(userItem);
            });
        } catch (error) {
        }
    },

    /**
     * Create a new user
     */
    async createUser(event) {
        event.preventDefault();
        const form = event.target;
        const formData = new FormData(form);

        try {
            const response = await fetch('/api/users', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    username: formData.get('username'),
                    email: formData.get('email'),
                    password: formData.get('password'),
                    is_admin: formData.get('is_admin') === 'on'
                })
            });

            if (response.ok) {
                form.reset();
                await this.loadUsers();
                this.showNotification('User created successfully', 'success');
            } else {
                const error = await response.json();
                this.showNotification(error.error || 'Failed to create user', 'error');
            }
        } catch (error) {
            this.showNotification('Failed to create user', 'error');
        }
    },

    /**
     * Delete a user
     */
    async deleteUser(userId) {
        if (!confirm('Are you sure you want to delete this user?')) {
            return;
        }

        try {
            const response = await fetch(`/api/users/${userId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                await this.loadUsers();
                this.showNotification('User deleted successfully', 'success');
            } else {
                const error = await response.json();
                this.showNotification(error.error || 'Failed to delete user', 'error');
            }
        } catch (error) {
            this.showNotification('Failed to delete user', 'error');
        }
    },

    /**
     * Show API tokens management
     */
    async showApiTokens() {
        const modal = document.getElementById('apiTokensModal');
        if (!modal) {
            this.createApiTokensModal();
        }

        await this.loadApiTokens();
        document.getElementById('apiTokensModal').style.display = 'block';
    },

    /**
     * Load API tokens
     */
    async loadApiTokens() {
        try {
            const response = await fetch('/api/tokens');
            const data = await response.json();

            const tokensList = document.getElementById('tokensList');
            tokensList.innerHTML = '';

            data.tokens.forEach(token => {
                const tokenItem = document.createElement('div');
                tokenItem.className = 'exclusion-item';
                const tokenDetails = [];
                tokenDetails.push(`Created: ${new Date(token.created_at).toLocaleDateString()}`);
                if (token.last_used) {
                    tokenDetails.push(`Last used: ${new Date(token.last_used).toLocaleDateString()}`);
                }
                if (token.expires_at) {
                    tokenDetails.push(`Expires: ${new Date(token.expires_at).toLocaleDateString()}`);
                }
                tokenItem.innerHTML = `
                    <span>
                        <strong>${token.description || 'Unnamed Token'}</strong><br>
                        <small style="color: var(--text-secondary);">${tokenDetails.join(' | ')}</small>
                    </span>
                    <button class="btn btn-sm btn-danger" onclick="AuthManager.deleteToken(${token.id})">
                        <i class="fas fa-trash"></i>
                    </button>
                `;
                tokensList.appendChild(tokenItem);
            });
        } catch (error) {
        }
    },

    /**
     * Create a new API token
     */
    async createToken(event) {
        event.preventDefault();
        const form = event.target;
        const formData = new FormData(form);

        try {
            const response = await fetch('/api/tokens', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    description: formData.get('description'),
                    expires_in_days: formData.get('expires_in_days') || null
                })
            });

            if (response.ok) {
                const data = await response.json();

                // Show the token to the user (only time it's visible)
                this.showTokenDisplay(data.token);

                form.reset();
                await this.loadApiTokens();
            } else {
                const error = await response.json();
                this.showNotification(error.error || 'Failed to create token', 'error');
            }
        } catch (error) {
            this.showNotification('Failed to create token', 'error');
        }
    },

    /**
     * Delete an API token
     */
    async deleteToken(tokenId) {
        if (!confirm('Are you sure you want to delete this token?')) {
            return;
        }

        try {
            const response = await fetch(`/api/tokens/${tokenId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                await this.loadApiTokens();
                this.showNotification('Token deleted successfully', 'success');
            } else {
                const error = await response.json();
                this.showNotification(error.error || 'Failed to delete token', 'error');
            }
        } catch (error) {
            this.showNotification('Failed to delete token', 'error');
        }
    },

    /**
     * Show change password modal
     */
    showChangePassword() {
        const modal = document.getElementById('changePasswordModal');
        if (!modal) {
            this.createChangePasswordModal();
        }
        document.getElementById('changePasswordModal').style.display = 'block';
    },

    /**
     * Change password
     */
    async changePassword(event) {
        event.preventDefault();
        const form = event.target;
        const formData = new FormData(form);

        const newPassword = formData.get('new_password');
        const confirmPassword = formData.get('confirm_password');

        if (newPassword !== confirmPassword) {
            this.showNotification('Passwords do not match', 'error');
            return;
        }

        try {
            const response = await fetch(`/api/users/${this.currentUser.id}/password`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    current_password: formData.get('current_password'),
                    new_password: newPassword
                })
            });

            if (response.ok) {
                form.reset();
                document.getElementById('changePasswordModal').style.display = 'none';
                this.showNotification('Password changed successfully', 'success');
            } else {
                const error = await response.json();
                this.showNotification(error.error || 'Failed to change password', 'error');
            }
        } catch (error) {
            this.showNotification('Failed to change password', 'error');
        }
    },

    /**
     * Show a notification
     */
    showNotification(message, type = 'info') {
        // Use existing notification system if available
        if (window.app && window.app.showNotification) {
            window.app.showNotification(message, type);
        } else {
            alert(message);
        }
    },

    /**
     * Show token display modal
     */
    showTokenDisplay(token) {
        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">API Token Created</h3>
                    <button class="modal-close" onclick="this.closest('.modal').remove()">×</button>
                </div>
                <div class="modal-body">
                    <div class="exclusions-section">
                        <div style="padding: 1rem 0; color: var(--text-primary);">
                            <p style="margin-bottom: 1rem;"><strong>Important:</strong> Copy this token now. You won't be able to see it again!</p>
                            <div class="exclusion-input-group">
                                <input type="text" value="${token}" readonly id="tokenValue" class="form-control" style="font-family: monospace;">
                                <button class="btn btn-primary" onclick="AuthManager.copyToken()">
                                    <i class="fas fa-copy"></i> Copy
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.style.display = 'block';
    },

    /**
     * Copy token to clipboard
     */
    copyToken() {
        const tokenInput = document.getElementById('tokenValue');
        tokenInput.select();
        document.execCommand('copy');
        this.showNotification('Token copied to clipboard', 'success');
    },

    /**
     * Create user management modal (if it doesn't exist)
     */
    createUserManagementModal() {
        // This would normally be in the HTML, but creating it dynamically for modularity
        const modal = document.createElement('div');
        modal.id = 'userManagementModal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">User Management</h3>
                    <button class="modal-close" onclick="document.getElementById('userManagementModal').style.display='none'">×</button>
                </div>
                <div class="modal-body">
                    <div class="exclusions-section">
                        <h4>Create New User</h4>
                        <form onsubmit="AuthManager.createUser(event); return false;" style="margin-bottom: 1rem;">
                            <input type="text" name="username" class="form-control" placeholder="Username" required style="margin-bottom: 0.5rem;">
                            <input type="email" name="email" class="form-control" placeholder="Email" required style="margin-bottom: 0.5rem;">
                            <input type="password" name="password" class="form-control" placeholder="Password (min 8 characters)" required minlength="8" style="margin-bottom: 0.5rem;">
                            <label class="checkbox-label" style="display: block; margin-bottom: 1rem;">
                                <input type="checkbox" name="is_admin" checked>
                                <span style="margin-left: 0.5rem;">Admin Access</span>
                            </label>
                            <button type="submit" class="btn btn-primary">
                                <i class="fas fa-plus"></i> Create User
                            </button>
                        </form>
                    </div>

                    <div class="exclusions-section">
                        <h4>Existing Users</h4>
                        <div id="usersList" class="exclusion-list">
                            <div class="loading">Loading...</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    },

    /**
     * Create API tokens modal
     */
    createApiTokensModal() {
        const modal = document.createElement('div');
        modal.id = 'apiTokensModal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">API Tokens</h3>
                    <button class="modal-close" onclick="document.getElementById('apiTokensModal').style.display='none'">×</button>
                </div>
                <div class="modal-body">
                    <div class="exclusions-section">
                        <h4>Create New Token</h4>
                        <form onsubmit="AuthManager.createToken(event); return false;">
                            <div class="exclusion-input-group">
                                <input type="text" name="description" class="form-control" placeholder="Token description" required>
                                <input type="number" name="expires_in_days" class="form-control" placeholder="Days" min="1" style="max-width: 100px;" title="Leave empty for no expiration">
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-plus"></i> Add
                                </button>
                            </div>
                            <small style="color: var(--text-secondary); display: block; margin-top: 0.5rem;">Leave expiration days empty for tokens that never expire</small>
                        </form>
                    </div>

                    <div class="exclusions-section">
                        <h4>Existing Tokens</h4>
                        <div id="tokensList" class="exclusion-list">
                            <div class="loading">Loading...</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    },

    /**
     * Create change password modal
     */
    createChangePasswordModal() {
        const modal = document.createElement('div');
        modal.id = 'changePasswordModal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3 class="modal-title">Change Password</h3>
                    <button class="modal-close" onclick="document.getElementById('changePasswordModal').style.display='none'">×</button>
                </div>
                <div class="modal-body">
                    <div class="exclusions-section">
                        <form onsubmit="AuthManager.changePassword(event); return false;">
                            <input type="password" name="current_password" class="form-control" placeholder="Current Password" required style="margin-bottom: 0.5rem;">
                            <input type="password" name="new_password" class="form-control" placeholder="New Password (min 8 characters)" required minlength="8" style="margin-bottom: 0.5rem;">
                            <input type="password" name="confirm_password" class="form-control" placeholder="Confirm New Password" required minlength="8" style="margin-bottom: 1rem;">
                            <button type="submit" class="btn btn-primary">
                                <i class="fas fa-lock"></i> Change Password
                            </button>
                        </form>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
};

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    AuthManager.init();
});

// Export for use in other modules
window.AuthManager = AuthManager;