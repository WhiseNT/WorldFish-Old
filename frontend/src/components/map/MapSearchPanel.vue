<template>
  <section class="search-panel">
    <div class="search-row">
      <input v-model="query" class="search-input" placeholder="搜索区域、势力、资源、事件..." @keyup.enter="search" />
      <button class="search-btn" :disabled="loading" @click="search">{{ loading ? '搜索中' : '搜索' }}</button>
    </div>
    <div v-if="results.length" class="results">
      <button v-for="item in results" :key="item.id" class="result-item" @click="$emit('select-result', item)">
        <strong>{{ item.name || `区域 ${item.q},${item.r}` }}</strong>
        <span>{{ item.faction || '无势力' }} · {{ item.terrain }} · {{ item.status }}</span>
      </button>
    </div>
    <p v-else-if="searched" class="empty">没有找到匹配区域。</p>
  </section>
</template>

<script>
export default {
  name: 'MapSearchPanel',
  props: { loading: { type: Boolean, default: false }, results: { type: Array, default: () => [] } },
  emits: ['search', 'select-result'],
  data() {
    return { query: '', searched: false }
  },
  methods: {
    search() {
      this.searched = true
      this.$emit('search', this.query)
    },
  },
}
</script>

<style scoped>
.search-panel { border: 1px solid var(--wf-border); border-radius: var(--radius-lg); background: var(--wf-bg-card); color: var(--wf-text-primary); padding: var(--spacing-md); }
.search-row { display: flex; gap: var(--spacing-sm); }
.search-input { flex: 1; border: 1px solid var(--wf-border); border-radius: var(--radius-md); padding: var(--spacing-sm) var(--spacing-md); background: var(--wf-bg-input); color: var(--wf-text-primary); outline: none; }
.search-input::placeholder { color: var(--wf-text-muted); }
.search-input:focus { border-color: var(--wf-accent); box-shadow: 0 0 0 3px var(--wf-accent-muted); }
.search-btn { border: 1px solid var(--wf-accent); border-radius: var(--radius-md); padding: var(--spacing-sm) var(--spacing-md); background: var(--wf-accent); color: var(--wf-text-on-accent); font-weight: 600; cursor: pointer; }
.results { display: grid; gap: var(--spacing-sm); margin-top: var(--spacing-sm); max-height: 180px; overflow: auto; }
.result-item { text-align: left; border: 1px solid var(--wf-border); border-radius: var(--radius-md); padding: var(--spacing-sm) var(--spacing-md); background: var(--wf-bg-hover); cursor: pointer; }
.result-item:hover { border-color: var(--wf-border-light); background: rgba(255, 255, 255, 0.08); }
.result-item strong { display: block; color: var(--wf-text-primary); }
.result-item span { color: var(--wf-text-secondary); font-size: 12px; }
.empty { color: var(--wf-text-muted); font-size: 13px; }
</style>
