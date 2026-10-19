import http from "@/api";

// 管理员登录（手机号 + 个人密码）
export const adminLoginApi = (params: { phone: string; pin: string }) => {
	return http.post<any>("/auth/admin/login", params);
};

// 看板统计
export const getDashboardApi = () => {
	return http.get<any>("/admin/dashboard");
};

// 运营总览看板（趋势 / 订阅转化 / 激活码使用）
export const getDashboardOverviewApi = () => {
	return http.get<any>("/admin/dashboard/overview");
};

// 会员订阅列表（看板会员管理入口）
export const getDashboardMembersApi = (params: { limit?: number }) => {
	return http.get<any>("/admin/dashboard/members", params);
};

// 激活码列表
export const getActivationCodesApi = (params: { page: number; size: number; used?: boolean }) => {
	return http.get<any>("/admin/activation-codes", params);
};

// 生成激活码
export const createActivationCodesApi = (params: { count: number; label?: string }) => {
	return http.post<any>("/admin/activation-codes", params);
};

// 用户列表
export const getUsersApi = (params: { page: number; size: number; role?: string; keyword?: string }) => {
	return http.get<any>("/admin/users", params);
};

// 冻结/解冻用户
export const freezeUserApi = (userId: string) => {
	return http.post<any>(`/admin/users/${userId}/freeze`);
};

export const unfreezeUserApi = (userId: string) => {
	return http.post<any>(`/admin/users/${userId}/unfreeze`);
};

// 错题列表
export const getMistakesApi = (params: { page: number; size: number; student_id?: string }) => {
	return http.get<any>("/admin/mistakes", params);
};

// 掌握度列表
export const getMasteryApi = (params: { page: number; size: number; student_id?: string }) => {
	return http.get<any>("/admin/mastery", params);
};

// AI 会话列表
export const getAiSessionsApi = (params: { page: number; size: number; student_id?: string }) => {
	return http.get<any>("/admin/ai-sessions", params);
};
