import { Login } from "@/api/interface/index";

import http from "@/api";

/**
 * @name 登录模块
 * 说明：菜单与按钮权限均为本地静态配置（后端不下发菜单），
 * 前端只依赖后端 JWT 鉴权，接口越权由后端统一返回 403。
 */
// * 用户登录接口
export const loginApi = (params: Login.ReqLoginForm) => {
	return http.post<Login.ResLogin>(`/auth/admin/login`, params);
};

// * 获取按钮权限（本地静态返回，不请求后端）
export const getAuthButtons = () => {
	return Promise.resolve({ data: {} as Login.ResAuthButtons });
};

// * 获取菜单列表（本地静态菜单，path 与路由模块保持一一对应）
export const getMenuList = () => {
	const menus: Menu.MenuOptions[] = [
		{ path: "/payments", title: "支付确认", icon: "Money" },
		{ path: "/dashboard", title: "数据看板", icon: "Odometer" },
		{ path: "/activation", title: "激活码管理", icon: "Ticket" },
		{ path: "/users", title: "用户管理", icon: "UserFilled" },
		{ path: "/mistakes", title: "错题管理", icon: "Document" },
		{ path: "/mastery", title: "掌握度", icon: "TrendCharts" },
		{ path: "/ai-sessions", title: "AI 会话日志", icon: "ChatDotRound" },
		{ path: "/config", title: "系统配置", icon: "Setting" }
	];
	return Promise.resolve({ data: menus });
};
