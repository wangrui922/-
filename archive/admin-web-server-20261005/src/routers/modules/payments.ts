import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// 支付确认模块（V4.2 人工确认支付）
const paymentsRouter: Array<RouteRecordRaw> = [
	{
		path: "/payments",
		component: Layout,
		children: [
			{
				path: "",
				name: "payments",
				component: () => import("@/views/payments/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "支付确认",
					key: "payments"
				}
			}
		]
	}
];

export default paymentsRouter;
