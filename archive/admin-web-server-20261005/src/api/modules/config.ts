import http from "@/api";

// 获取系统配置列表
export const getSystemConfigsApi = () => {
	return http.get<any>("/admin/configs");
};

// 更新单个配置
export const updateSystemConfigApi = (key: string, value: string) => {
	return http.put<any>(`/admin/configs/${key}`, { value });
};

// 重置所有配置为默认值
export const resetSystemConfigsApi = () => {
	return http.post<any>("/admin/configs/reset");
};
