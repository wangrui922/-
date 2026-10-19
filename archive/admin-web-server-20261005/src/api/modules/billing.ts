import http from "@/api";

// 待确认支付列表（仅管理员）
export const getPendingPaymentsApi = () => {
	return http.get<any>("/billing/pending");
};

// 确认支付
export const confirmPaymentApi = (params: { subscription_id: string; confirm: boolean; reject_reason?: string }) => {
	return http.post<any>("/billing/confirm", params);
};
