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
            console.error('Failed to check auth status:', error);
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
            await fetch('/api/auth/logout', { method: 'POST' });
            window.location.href = '/login';
        } catch (error) {
            console.error('Logout failed:', error);
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
                userItem.className = 'user-item';
                userItem.innerHTML = `
                    <div class="user-info">
                        <strong>${user.username}</strong>
                        <span>${user.email}</span>
                        ${user.is_admin ? '<span class="badge admin">Admin</span>' : ''}
                    </div>
                    <div class="user-actions">
                        ${user.id !== this.currentUser.id ?
                            `<button class="btn btn-danger btn-sm" onclick="AuthManager.deleteUser(${user.id})">Delete</button>` :
                            ''}
                    </div>
                `;
                usersList.appendChild(userItem);
            });
        } catch (error) {
            console.error('Failed to load users:', error);
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
                tokenItem.className = 'token-item';
                tokenItem.innerHTML = `
                    <div class="token-info">
                        <strong>${token.description || 'Unnamed Token'}</strong>
                        <span>Created: ${new Date(token.created_at).toLocaleDateString()}</span>
                        ${token.last_used ? `<span>Last used: ${new Date(token.last_used).toLocaleDateString()}</span>` : ''}
                        ${token.expires_at ? `<span>Expires: ${new Date(token.expires_at).toLocaleDateString()}</span>` : ''}
                    </div>
                    <div class="token-actions">
                        <button class="btn btn-danger btn-sm" onclick="AuthManager.deleteToken(${token.id})">Delete</button>
                    </div>
                `;
                tokensList.appendChild(tokenItem);
            });
        } catch (error) {
            console.error('Failed to load tokens:', error);
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
                    <h2>API Token Created</h2>
                    <button class="close-btn" onclick="this.closest('.modal').remove()">×</button>
                </div>
                <div class="modal-body">
                    <p><strong>Important:</strong> Copy this token now. You won't be able to see it again!</p>
                    <div class="token-display">
                        <input type="text" value="${token}" readonly id="tokenValue">
                        <button class="btn btn-primary" onclick="AuthManager.copyToken()">Copy</button>
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
                    <h2>User Management</h2>
                    <button class="close-btn" onclick="document.getElementById('userManagementModal').style.display='none'">×</button>
                </div>
                <div class="modal-body">
                    <h3>Create New User</h3>
                    <form onsubmit="AuthManager.createUser(event)">
                        <input type="text" name="username" placeholder="Username" required>
                        <input type="email" name="email" placeholder="Email" required>
                        <input type="password" name="password" placeholder="Password (min 8 characters)" required minlength="8">
                        <label>
                            <input type="checkbox" name="is_admin" checked> Admin Access
                        </label>
                        <button type="submit" class="btn btn-primary">Create User</button>
                    </form>

                    <h3>Existing Users</h3>
                    <div id="usersList"></div>
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
                    <h2>API Tokens</h2>
                    <button class="close-btn" onclick="document.getElementById('apiTokensModal').style.display='none'">×</button>
                </div>
                <div class="modal-body">
                    <h3>Create New Token</h3>
                    <form onsubmit="AuthManager.createToken(event)">
                        <input type="text" name="description" placeholder="Token description" required>
                        <input type="number" name="expires_in_days" placeholder="Expires in days (optional)">
                        <button type="submit" class="btn btn-primary">Create Token</button>
                    </form>

                    <h3>Existing Tokens</h3>
                    <div id="tokensList"></div>
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
                    <h2>Change Password</h2>
                    <button class="close-btn" onclick="document.getElementById('changePasswordModal').style.display='none'">×</button>
                </div>
                <div class="modal-body">
                    <form onsubmit="AuthManager.changePassword(event)">
                        <input type="password" name="current_password" placeholder="Current Password" required>
                        <input type="password" name="new_password" placeholder="New Password (min 8 characters)" required minlength="8">
                        <input type="password" name="confirm_password" placeholder="Confirm New Password" required minlength="8">
                        <button type="submit" class="btn btn-primary">Change Password</button>
                    </form>
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