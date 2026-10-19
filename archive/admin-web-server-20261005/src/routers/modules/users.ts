import { RouteRecordRaw } from "vue-router";
import { Layout } from "@/routers/constant";

// 用户管理模块
const usersRouter: Array<RouteRecordRaw> = [
	{
		path: "/users",
		component: Layout,
		children: [
			{
				path: "",
				name: "users",
				component: () => import("@/views/users/index.vue"),
				meta: {
					keepAlive: true,
					requiresAuth: true,
					title: "用户管理",
					key: "users"
				}
			}
		]
	}
];

export default usersRouter;
