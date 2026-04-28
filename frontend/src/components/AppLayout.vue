<template>
  <div class="app-shell">
    <nav class="wf-navbar">
      <div class="wf-navbar-left">
        <router-link to="/" class="wf-brand">
          <span class="wf-brand-text">WORLDFISH</span>
          <span class="wf-brand-accent"></span>
        </router-link>
      </div>
      <div class="wf-navbar-center">
        <slot name="nav-center"></slot>
      </div>
      <div class="wf-navbar-right">
        <slot name="nav-right"></slot>
      </div>
    </nav>
    <main class="wf-content">
      <slot></slot>
    </main>
  </div>
</template>

<script setup>
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--wf-bg-primary);
}

.wf-navbar {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--spacing-lg);
  background: rgba(9, 9, 11, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;
  z-index: 100;
}

.wf-navbar-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.wf-brand {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  text-decoration: none;
}

.wf-brand-text {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 700;
  color: var(--wf-text-primary);
  letter-spacing: 2px;
}

.wf-brand-accent {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--wf-accent);
  box-shadow: 0 0 8px var(--wf-accent-glow);
}

.wf-navbar-center {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.wf-navbar-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.wf-content {
  flex: 1;
  overflow-y: auto;
}
</style>
