<template>
  <div class="evo-page">
    <div class="evo-header">
      <StepIndicator
        :currentStep="status === 'completed' ? 3 : 2"
        :totalSteps="3"
        :stepLabels="['推演设置', '推演进化', '结果']"
      />
      <div class="evo-header-right">
        <span v-if="evoType === 'branch'" class="tag tag-accent">重新推演</span>
        <span v-else class="tag tag-primary">向后推演</span>
        <button v-if="status === 'completed'" class="btn btn-accent btn-sm" @click="showApplyDialog = true">
          变更实体
        </button>
      </div>
    </div>

    <div v-if="loading" class="state-box"><div class="loading-spinner"></div><p>加载中...</p></div>
    <div v-else-if="loadError" class="state-box"><p class="message-error">{{ loadError }}</p></div>

    <div v-else class="evo-layout">
      <!-- 左：知识图谱 -->
      <div class="evo-graph-panel">
        <GraphPanel
          v-if="graphData"
          :graphData="graphData"
          :loading="false"
          :currentPhase="2"
          :isSimulating="false"
          @refresh="loadGraph"
          @toggle-maximize="toggleGraphMax"
          @entity-click="onEntityClick"
        />
        <div v-else class="graph-placeholder">图谱加载中...</div>
      </div>

      <!-- 右：推演轮次 -->
      <div class="evo-right">
        <div v-if="status === 'running'" class="progress-section">
          <div class="progress-info">
            <span>第 {{ currentRound }} / {{ totalRounds }} 轮</span>
            <span class="progress-pct">{{ Math.round((currentRound / totalRounds) * 100) }}%</span>
          </div>
          <div class="progress-bar"><div class="progress-fill" :style="{ width: (currentRound / totalRounds * 100) + '%' }"></div></div>
        </div>

        <div class="rounds-scroll">
          <div v-for="round in rounds" :key="round.round_number" class="round-card card fade-in">
            <div class="round-header">
              <span class="round-badge">第 {{ round.round_number }} 轮</span>
              <span v-if="round.year_advanced_to" class="round-year">{{ round.year_advanced_to }}</span>
            </div>
            <div class="round-narrative">{{ round.narrative }}</div>

            <div v-if="round.affected_entities?.length" class="round-entities">
              <h4>变化的实体</h4>
              <div v-for="ent in round.affected_entities" :key="ent.name" class="entity-change">
                <span class="ent-name">{{ ent.name }}</span>
                <span class="tag tag-primary">{{ ent.new_status }}</span>
              </div>
            </div>

            <div v-if="round.new_events?.length" class="round-events">
              <h4>新事件</h4>
              <div v-for="evt in round.new_events" :key="evt.name" class="mini-event">
                <span class="evt-name">{{ evt.name }}</span>
                <span v-if="evt.date" class="evt-date">{{ evt.date }}</span>
              </div>
            </div>

            <div class="round-actions">
              <button class="btn btn-secondary btn-xs" @click="startBranchFrom(round.round_number)">从此轮重新推演</button>
            </div>
          </div>

          <div v-if="rounds.length === 0 && status === 'running'" class="state-box">
            <div class="loading-spinner"></div><p>正在生成第一轮...</p>
          </div>

          <!-- 向后推演 -->
          <div v-if="status === 'completed'" class="forward-section card">
            <h4>向后推演 — 从当前终点继续</h4>
            <textarea v-model="forwardScenario" class="scenario-input" rows="2" placeholder="继续推演的场景..."></textarea>
            <div class="forward-params">
              <label>轮次 <input v-model.number="forwardRounds" type="number" min="1" max="10" /></label>
              <button class="btn btn-primary btn-sm" :disabled="!forwardScenario.trim() || forwarding" @click="startForward">
                {{ forwarding ? '...' : '向后推演 →' }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="status === 'failed'" class="state-box"><p class="message-error">推演失败</p></div>
      </div>
    </div>

    <!-- 变更实体弹窗 -->
    <div v-if="showApplyDialog" class="dialog-overlay" @click.self="showApplyDialog = false">
      <div class="dialog-content apply-dialog">
        <div class="dialog-header">
          <h2>变更实体 — 应用到世界观</h2>
          <button class="close-btn" @click="showApplyDialog = false">&times;</button>
        </div>
        <div class="dialog-body">
          <p class="hint">勾选你想应用的变更，它们将添加回世界观设定中</p>
          <div class="apply-list">
            <div v-for="round in rounds" :key="'apply-' + round.round_number" class="apply-round-group">
              <h5>第 {{ round.round_number }} 轮</h5>
              <label v-for="ent in (round.affected_entities || [])" :key="ent.name" class="apply-checkbox">
                <input type="checkbox" :value="{ round: round.round_number, name: ent.name, state_changes: ent.state_changes, new_status: ent.new_status }" v-model="selectedEntities" />
                <span class="apply-ent-name">{{ ent.name }}</span>
                <span class="apply-ent-change">{{ ent.state_changes }}</span>
              </label>
              <label v-for="evt in (round.new_events || [])" :key="'evt-' + evt.name" class="apply-checkbox">
                <input type="checkbox" :value="evt" v-model="selectedEvents" />
                <span class="apply-ent-name">事件: {{ evt.name }}</span>
                <span class="apply-ent-change">{{ evt.date }}</span>
              </label>
            </div>
          </div>
        </div>
        <div class="dialog-footer">
          <button class="btn btn-secondary" @click="showApplyDialog = false">取消</button>
          <button class="btn btn-accent" :disabled="(!selectedEntities.length && !selectedEvents.length) || applying" @click="applyChanges">
            {{ applying ? '应用中...' : `应用 (${selectedEntities.length} 实体, ${selectedEvents.length} 事件)` }}
          </button>
        </div>
        <p v-if="applyMsg" class="message-success">{{ applyMsg }}</p>
        <p v-if="applyErr" class="message-error">{{ applyErr }}</p>
      </div>
    </div>

    <!-- 实体对话弹窗 -->
    <div v-if="chatEntity" class="dialog-overlay" @click.self="chatEntity = null">
      <div class="dialog-content entity-chat-dialog">
        <div class="dialog-header">
          <h2>与 {{ chatEntity.name }} 对话</h2>
          <button class="close-btn" @click="chatEntity = null">&times;</button>
        </div>
        <!-- 阶段选择 -->
        <div v-if="chatStages.length > 0" class="stage-selector">
          <label class="stage-label">角色阶段：</label>
          <select v-model="selectedStage" class="form-input stage-select">
            <option value="">默认（当前状态）</option>
            <option v-for="s in chatStages" :key="s['名称'] || s.name" :value="s['名称'] || s.name">
              {{ s['名称'] || s.name }}{{ (s['时期'] || s.era) ? ' (' + (s['时期'] || s.era) + ')' : '' }}
            </option>
          </select>
        </div>
        <div class="chat-messages">
          <div v-for="(msg, i) in chatMessages" :key="i" :class="['chat-msg', msg.role]">{{ msg.content }}</div>
          <div v-if="chatLoading" class="chat-msg assistant">思考中...</div>
        </div>
        <div class="chat-input-row">
          <input v-model="chatInput" class="form-input chat-input" placeholder="输入你的问题..." @keyup.enter="sendChat" />
          <button class="btn btn-primary" :disabled="chatLoading" @click="sendChat">发送</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StepIndicator from '../components/StepIndicator.vue'
import GraphPanel from '../components/GraphPanel.vue'
import { default as service } from '../api/index'

const route = useRoute()
const router = useRouter()
const evolutionId = ref(route.params.id)

const loading = ref(true)
const loadError = ref('')
const status = ref('running')
const worldId = ref('')
const rounds = ref([])
const currentRound = ref(0)
const totalRounds = ref(5)
const evoType = ref('forward')
const parentEvoId = ref('')
let pollTimer = null

const graphData = ref(null)
const showApplyDialog = ref(false)

async function loadGraph() {
  if (!worldId.value) return
  try {
    const res = await service.get(`/api/world/${worldId.value}`)
    const w = res.world || {}
    const nodes = (w.entities || []).map((e, i) => ({
      uuid: e.id || `ent-${i}`, name: e.name, labels: [e.type || 'Entity'],
      summary: Object.entries(e.attributes || {}).map(([k, v]) => `${k}: ${v}`).join('; '),
      attributes: e.attributes || {},
      stages: Array.isArray(e.stages) ? e.stages : [],
    }))
    const edges = []
    for (const evt of w.events || []) {
      const ents = evt.entities || []
      for (let i = 0; i < ents.length; i++)
        for (let j = i + 1; j < ents.length; j++) {
          const src = nodes.find(n => n.name === ents[i])
          const tgt = nodes.find(n => n.name === ents[j])
          if (src && tgt) edges.push({ uuid: `e-${edges.length}`, name: 'RELATED_TO', fact: evt.name, source_node_uuid: src.uuid, target_node_uuid: tgt.uuid, attributes: {} })
        }
    }
    graphData.value = { graph_id: worldId.value, nodes, edges, node_count: nodes.length, edge_count: edges.length }
  } catch (e) { /* ignore */ }
}

function toggleGraphMax() {}

async function fetchEvolution() {
  try {
    const res = await service.get(`/api/evolution/${evolutionId.value}`)
    const evo = res.evolution || res
    status.value = evo.status || 'running'
    worldId.value = evo.world_id || ''
    rounds.value = evo.rounds || []
    currentRound.value = rounds.value.length
    totalRounds.value = (evo.config && evo.config.rounds) || 5
    evoType.value = evo.evolution_type || 'forward'
    parentEvoId.value = evo.parent_evolution_id || ''
    if (worldId.value) loadGraph()
  } catch (e) { loadError.value = '加载失败: ' + (e.message || '') } finally { loading.value = false }
}

function startPolling() {
  pollTimer = setInterval(async () => {
    try {
      const res = await service.get(`/api/evolution/${evolutionId.value}`)
      const evo = res.evolution || res
      status.value = evo.status || 'running'
      rounds.value = evo.rounds || []
      currentRound.value = rounds.value.length
      if (!worldId.value && evo.world_id) { worldId.value = evo.world_id; loadGraph() }
      if (status.value === 'completed' || status.value === 'failed') { clearInterval(pollTimer); pollTimer = null }
    } catch (e) { /* ignore */ }
  }, 3000)
}

onMounted(async () => { await fetchEvolution(); if (status.value === 'running') startPolling() })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })

function startBranchFrom(roundNum) {
  router.push({ name: 'SimulationSetup', query: { worldId: worldId.value, parentEvolutionId: evolutionId.value, parentRound: roundNum, evolutionType: 'branch' } })
}

const forwardScenario = ref('')
const forwardRounds = ref(3)
const forwarding = ref(false)
async function startForward() {
  forwarding.value = true
  try {
    const lastRound = rounds.value[rounds.value.length - 1]
    const lastYear = lastRound?.year_advanced_to || lastRound?.raw_response?.year_advanced_to || ''
    const config = { rounds: forwardRounds.value, temperature: 0.7 }
    if (lastYear) {
      config.time_span_start = lastYear
    }
    const res = await service.post('/api/evolution/create', {
      world_id: worldId.value, scenario: forwardScenario.value,
      config,
      evolution_type: 'forward', parent_evolution_id: evolutionId.value,
      parent_round: rounds.value.length - 1,
    })
    router.push({ name: 'SimulationEvolution', params: { id: res.evolution_id } })
  } catch (e) { alert('启动失败: ' + (e.message || '')) } finally { forwarding.value = false }
}

const selectedEntities = ref([])
const selectedEvents = ref([])
const applying = ref(false)
const applyMsg = ref('')
const applyErr = ref('')

async function applyChanges() {
  applying.value = true; applyMsg.value = ''; applyErr.value = ''
  try {
    const res = await service.post(`/api/evolution/${evolutionId.value}/apply`, {
      entities: selectedEntities.value.map(e => ({ name: e.name, state_changes: e.state_changes, new_status: e.new_status })),
      events: selectedEvents.value,
      rounds: [...new Set(selectedEntities.value.map(e => e.round))],
    })
    applyMsg.value = res.message || '应用成功'
    selectedEntities.value = []
    selectedEvents.value = []
  } catch (e) { applyErr.value = '应用失败: ' + (e.message || '') } finally { applying.value = false }
}

// Entity chat
const chatEntity = ref(null)
const chatMessages = ref([])
const chatInput = ref('')
const chatLoading = ref(false)
const chatStages = ref([])
const selectedStage = ref('')

function onEntityClick(entityData) {
  chatEntity.value = entityData
  chatMessages.value = []
  chatInput.value = ''
  selectedStage.value = ''
  const attrs = entityData.attributes || {}
  const stages = Array.isArray(entityData.stages) && entityData.stages.length > 0
    ? entityData.stages
    : (attrs['阶段'] || attrs['stages'] || [])
  chatStages.value = Array.isArray(stages) ? stages.filter(s => typeof s === 'object') : []
}

async function sendChat() {
  if (!chatInput.value.trim() || chatLoading.value) return
  chatLoading.value = true
  const userMsg = chatInput.value
  chatMessages.value.push({ role: 'user', content: userMsg })
  chatInput.value = ''
  try {
    const res = await service.post('/api/evolution/entity-chat', {
      world_id: worldId.value,
      entity_name: chatEntity.value.name,
      question: userMsg,
      stage_name: selectedStage.value || '',
    })
    chatMessages.value.push({ role: 'assistant', content: res.reply || '...' })
    // 更新可用阶段列表
    if (res.available_stages && res.available_stages.length > 0) {
      chatStages.value = res.available_stages
    }
  } catch (e) {
    chatMessages.value.push({ role: 'assistant', content: '对话失败: ' + (e.message || '') })
  } finally {
    chatLoading.value = false
  }
}
</script>

<style scoped>
.evo-page { height: calc(100vh - 56px); display: flex; flex-direction: column; background: var(--wf-bg-primary); }
.evo-header { display: flex; justify-content: space-between; align-items: center; padding: var(--spacing-md) var(--spacing-lg); border-bottom: 1px solid rgba(255, 255, 255, 0.06); }
.evo-header-right { display: flex; gap: var(--spacing-sm); align-items: center; }

.state-box { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: var(--spacing-md); padding: var(--spacing-2xl); flex: 1; }
.evo-layout { display: flex; flex: 1; overflow: hidden; }
.evo-graph-panel { width: 50%; border-right: 1px solid rgba(255, 255, 255, 0.06); overflow-y: auto; background: var(--wf-bg-primary); }
.evo-graph-panel :deep(.graph-panel) { height: 100%; }
.evo-graph-panel :deep(.graph-container) { height: calc(100% - 44px); }
.graph-placeholder { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--wf-text-muted); }

.evo-right { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

.progress-section { background: rgba(255, 255, 255, 0.03); border-bottom: 1px solid rgba(255, 255, 255, 0.06); padding: var(--spacing-sm) var(--spacing-lg); }
.progress-info { display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--wf-text-secondary); margin-bottom: 4px; }
.progress-pct { font-weight: 600; color: var(--wf-accent); }
.progress-bar { height: 3px; background: rgba(255, 255, 255, 0.06); border-radius: 2px; overflow: hidden; }
.progress-fill { height: 100%; background: var(--wf-accent); transition: width 0.5s ease; box-shadow: 0 0 8px var(--wf-accent-glow); }

.rounds-scroll { flex: 1; overflow-y: auto; padding: var(--spacing-md); display: flex; flex-direction: column; gap: var(--spacing-md); }

.round-card { }
.round-header { display: flex; justify-content: space-between; margin-bottom: var(--spacing-sm); }
.round-badge { font-family: var(--font-mono); font-size: 0.85rem; font-weight: 600; color: var(--wf-accent); }
.round-year { font-size: 0.8rem; color: var(--wf-text-muted); }
.round-narrative { font-size: 0.9rem; line-height: 1.7; color: var(--wf-text-primary); white-space: pre-wrap; margin-bottom: var(--spacing-sm); }

.round-entities h4, .round-events h4 { font-size: 0.8rem; color: var(--wf-text-secondary); margin-bottom: 4px; }
.entity-change { padding: var(--spacing-sm); background: rgba(255, 255, 255, 0.04); border-radius: var(--radius-sm); margin-bottom: 4px; display: flex; align-items: baseline; gap: var(--spacing-sm); }
.ent-name { font-weight: 600; }
.mini-event { display: flex; gap: var(--spacing-sm); padding: 2px 0; font-size: 0.85rem; }
.evt-name { color: var(--wf-text-primary); }
.evt-date { color: var(--wf-accent); font-size: 0.8rem; }

.round-actions { margin-top: var(--spacing-sm); }
.btn-xs { padding: 4px 10px; font-size: 0.75rem; border-radius: var(--radius-full); }

.forward-section { }
.forward-section h4 { font-size: 0.9rem; color: var(--wf-text-primary); margin-bottom: 4px; }
.scenario-input { width: 100%; background: var(--wf-bg-input); border: 1px solid var(--wf-border); border-radius: var(--radius-md); color: var(--wf-text-primary); padding: var(--spacing-sm); resize: vertical; font-size: 0.85rem; }
.scenario-input:focus { outline: none; border-color: var(--wf-accent); }
.forward-params { display: flex; align-items: center; gap: var(--spacing-md); margin-top: var(--spacing-sm); }
.forward-params label { font-size: 0.85rem; color: var(--wf-text-secondary); }
.forward-params input { width: 60px; }

/* Apply Dialog */
.apply-dialog { max-width: 620px; max-height: 70vh; }
.dialog-body { overflow-y: auto; }
.hint { font-size: 0.8rem; color: var(--wf-text-muted); margin-bottom: var(--spacing-sm); }
.apply-list { max-height: 400px; overflow-y: auto; }
.apply-round-group { margin-bottom: var(--spacing-sm); }
.apply-round-group h5 { font-size: 0.8rem; color: var(--wf-text-muted); margin-bottom: 4px; }
.apply-checkbox { display: flex; align-items: flex-start; gap: var(--spacing-sm); padding: 4px 0; font-size: 0.85rem; cursor: pointer; }
.apply-ent-name { font-weight: 500; color: var(--wf-text-primary); min-width: 120px; }
.apply-ent-change { color: var(--wf-text-secondary); font-size: 0.8rem; flex: 1; }

/* Chat Dialog */
.entity-chat-dialog { max-width: 560px; height: 520px; display: flex; flex-direction: column; }
.chat-messages { flex: 1; overflow-y: auto; padding: var(--spacing-sm); display: flex; flex-direction: column; gap: var(--spacing-sm); }
.chat-msg { padding: var(--spacing-sm) var(--spacing-md); border-radius: var(--radius-md); font-size: 0.9rem; max-width: 85%; }
.chat-msg.user { background: var(--wf-accent-muted); color: var(--wf-accent); align-self: flex-end; border: 1px solid rgba(255, 255, 175, 0.12); }
.chat-msg.assistant { background: rgba(255, 255, 255, 0.04); color: var(--wf-text-secondary); align-self: flex-start; border: 1px solid var(--wf-border); }
.chat-input-row { display: flex; gap: var(--spacing-sm); padding: var(--spacing-sm); border-top: 1px solid var(--wf-border); }
.chat-input { flex: 1; }
.stage-selector { display: flex; align-items: center; gap: var(--spacing-sm); padding: var(--spacing-sm) var(--spacing-md); background: rgba(255, 255, 255, 0.03); border-bottom: 1px solid var(--wf-border); }
.stage-label { font-size: 0.85rem; color: var(--wf-text-secondary); white-space: nowrap; }
.stage-select { flex: 1; font-size: 0.85rem; }

.btn-sm { padding: 8px 20px; font-size: 13px; border-radius: var(--radius-full); }
</style>
