/**
 * 游戏化学习接口封装（方向六 · F7）。
 *
 * 对应后端 `backend/app/api/game.py` 的 10 个端点：
 *   GET  /api/game/hub             游戏大厅
 *   POST /api/game/speed/start     限时挑战开局
 *   POST /api/game/speed/answer    限时挑战逐题作答
 *   POST /api/game/speed/finish    限时挑战结算
 *   POST /api/game/battle/start    回合战斗开局
 *   POST /api/game/battle/answer   回合战斗逐题作答
 *   POST /api/game/battle/finish   回合战斗结算
 *   GET  /api/game/badges          徽章墙
 *   GET  /api/game/leaderboard     排行榜
 *   GET  /api/game/history         游戏历史
 *
 * 出入参一律 snake_case（C10）。
 */
import { get, post } from '@/api/http'

// ==================================================================== 类型

/** 游戏大厅数据。 */
export interface HubData {
  total_points: number
  weekly_points: number
  monthly_points: number
  speed_best_score: number
  battle_best_wave: number
  total_games: number
  badge_count: number
  badge_total: number
  recent_games: GameHistoryItem[]
}

/** 限时挑战开局出参。 */
export interface SpeedStartResult {
  game_session_id: string
  duration_sec: number
  questions: GameQuestion[]
}

/** 限时挑战逐题作答出参。 */
export interface SpeedAnswerResult {
  is_correct: boolean
  combo: number
  score_gain: number
}

/** 限时挑战结算出参。 */
export interface SpeedFinishResult {
  score: number
  correct_count: number
  wrong_count: number
  max_combo: number
  duration_sec: number
  new_badges: NewBadge[]
  point_summary: PointSummary
}

/** 回合战斗开局出参。 */
export interface BattleStartResult {
  game_session_id: string
  player_hp: number
  monster: MonsterInfo
  question: GameQuestion
}

/** 回合战斗逐题作答出参。 */
export interface BattleAnswerResult {
  is_correct: boolean
  player_hp: number
  monster_hp: number
  wave: number
  game_over: boolean
  next_monster?: MonsterInfo
  next_question?: GameQuestion
}

/** 回合战斗结算出参。 */
export interface BattleFinishResult {
  score: number
  correct_count: number
  wrong_count: number
  best_wave: number
  duration_sec: number
  new_badges: NewBadge[]
  point_summary: PointSummary
}

/** 徽章项。 */
export interface BadgeItem {
  badge_id: string
  name: string
  display_name: string
  description: string
  category: string
  rarity: string
  icon_url: string | null
  unlocked_at: string | null
  unlock_metadata: Record<string, unknown> | null
}

/** 排行榜项。 */
export interface LeaderboardItem {
  rank: number
  student_id: string
  student_name: string
  score: number
  games_count: number
  best_wave: number
}

/** 游戏历史项。 */
export interface GameHistoryItem {
  id: string
  game_type: string
  score: number
  correct_count: number
  wrong_count: number
  max_combo: number
  duration_sec: number
  difficulty_level: number
  battle_best_wave: number
  ended_at: string | null
}

/** 游戏题目（脱敏）。 */
export interface GameQuestion {
  id: string
  content: string
  image_url: string | null
  type: string
  options: Record<string, unknown> | null
  scenario_type: string | null
}

/** 怪物信息。 */
export interface MonsterInfo {
  wave: number
  name: string
  rarity: string
  hp: number
  atk: number
  player_atk: number
}

/** 新获得徽章。 */
export interface NewBadge {
  badge_id: string
  name: string
  display_name: string
  description: string
  category: string
  rarity: string
  icon_url: string | null
}

/** 积分汇总。 */
export interface PointSummary {
  total_points: number
  weekly_points: number
  monthly_points: number
  total_games: number
  speed_best_score: number
  battle_best_wave: number
}

// ==================================================================== 接口

/** 游戏大厅。 */
export function getHub(): Promise<HubData> {
  return get<HubData>('/game/hub')
}

/** 限时挑战开局。 */
export function startSpeedChallenge(params: {
  difficulty?: number
  scenario_type?: string
} = {}): Promise<SpeedStartResult> {
  const body: Record<string, unknown> = {}
  if (typeof params.difficulty === 'number') body.difficulty = params.difficulty
  if (params.scenario_type) body.scenario_type = params.scenario_type
  return post<SpeedStartResult>('/game/speed/start', body)
}

/** 限时挑战逐题作答。 */
export function submitSpeedAnswer(params: {
  game_session_id: string
  question_id: string
  student_answer: string
  elapsed_ms: number
}): Promise<SpeedAnswerResult> {
  return post<SpeedAnswerResult>('/game/speed/answer', params)
}

/** 限时挑战结算。 */
export function finishSpeedChallenge(game_session_id: string): Promise<SpeedFinishResult> {
  return post<SpeedFinishResult>('/game/speed/finish', { game_session_id })
}

/** 回合战斗开局。 */
export function startMathBattle(params: {
  difficulty?: number
} = {}): Promise<BattleStartResult> {
  const body: Record<string, unknown> = {}
  if (typeof params.difficulty === 'number') body.difficulty = params.difficulty
  return post<BattleStartResult>('/game/battle/start', body)
}

/** 回合战斗逐题作答。 */
export function submitBattleAnswer(params: {
  game_session_id: string
  question_id: string
  student_answer: string
}): Promise<BattleAnswerResult> {
  return post<BattleAnswerResult>('/game/battle/answer', params)
}

/** 回合战斗结算。 */
export function finishMathBattle(game_session_id: string): Promise<BattleFinishResult> {
  return post<BattleFinishResult>('/game/battle/finish', { game_session_id })
}

/** 徽章墙。 */
export function getBadges(): Promise<BadgeItem[]> {
  return get<BadgeItem[]>('/game/badges')
}

/** 排行榜。 */
export function getLeaderboard(params: {
  rank_type?: 'weekly' | 'monthly'
  period?: string
  top_n?: number
} = {}): Promise<LeaderboardItem[]> {
  const query = new URLSearchParams()
  if (params.rank_type) query.set('rank_type', params.rank_type)
  if (params.period) query.set('period', params.period)
  if (typeof params.top_n === 'number') query.set('top_n', String(params.top_n))
  const qs = query.toString()
  return get<LeaderboardItem[]>(`/game/leaderboard${qs ? '?' + qs : ''}`)
}

/** 游戏历史。 */
export function getGameHistory(params: {
  game_type?: 'speed_challenge' | 'math_battle'
  limit?: number
} = {}): Promise<GameHistoryItem[]> {
  const query = new URLSearchParams()
  if (params.game_type) query.set('game_type', params.game_type)
  if (typeof params.limit === 'number') query.set('limit', String(params.limit))
  const qs = query.toString()
  return get<GameHistoryItem[]>(`/game/history${qs ? '?' + qs : ''}`)
}
