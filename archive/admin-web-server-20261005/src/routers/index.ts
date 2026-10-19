import router from "@/routers/router";
import NProgress from "@/config/nprogress";
import { GlobalStore } from "@/store";
import { AxiosCanceler } from "@/api/helper/axiosCancel";

const axiosCanceler = new AxiosCanceler();

// * 路由拦截 beforeEach
router.beforeEach((to, from, next) => {
	NProgress.start();
	// * 在跳转路由之前，清除所有的请求
	axiosCanceler.removeAllPending();

	// * 判断当前路由是否需要访问权限
	if (!to.matched.some(record => record.meta.requiresAuth)) return next();

	// * 判断是否有 Token（菜单为本地静态配置，页面权限由后端接口按角色校验）
	const globalStore = GlobalStore();
	if (!globalStore.token) {
		next({
			path: "/login"
		});
		NProgress.done();
		return;
	}
	return next();
});

router.afterEach(() => {
	NProgress.done();
});

export default router;
