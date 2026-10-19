<template>
	<div class="dashboard-container">
		<!-- 核心运营指标 -->
		<el-row :gutter="20">
			<el-col :span="3" v-for="item in statCards" :key="item.key">
				<el-card shadow="hover" class="stat-card">
					<div class="stat-icon" :style="{ color: item.color, background: item.bg }">
						<el-icon :size="22">
							<component :is="item.icon" />
						</el-icon>
					</div>
					<div class="stat-info">
						<div class="stat-value">{{ item.value }}</div>
						<div class="stat-label">{{ item.label }}</div>
					</div>
				</el-card>
			</el-col>
		</el-row>

		<!-- 注册 / 活跃趋势 -->
		<el-row :gutter="20" style="margin-top: 20px">
			<el-col :span="12">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">近 14 天注册趋势</div>
					</template>
					<div ref="registerChartRef" class="chart-box"></div>
				</el-card>
			</el-col>
			<el-col :span="12">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">近 14 天日活跃用户（DAU）</div>
					</template>
					<div ref="dauChartRef" class="chart-box"></div>
				</el-card>
			</el-col>
		</el-row>

		<!-- 订阅转化 -->
		<el-row :gutter="20" style="margin-top: 20px">
			<el-col :span="8">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">订阅状态分布</div>
					</template>
					<div ref="subStatusChartRef" class="chart-box"></div>
				</el-card>
			</el-col>
			<el-col :span="8">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">套餐分布</div>
					</template>
					<div ref="subPlanChartRef" class="chart-box"></div>
				</el-card>
			</el-col>
			<el-col :span="8">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">近 30 天支付金额（元）</div>
					</template>
					<div ref="paymentChartRef" class="chart-box"></div>
				</el-card>
			</el-col>
		</el-row>

		<!-- 激活码使用 + 会员管理 -->
		<el-row :gutter="20" style="margin-top: 20px">
			<el-col :span="6">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">激活码使用情况</div>
					</template>
					<div ref="activationChartRef" class="chart-box"></div>
				</el-card>
			</el-col>
			<el-col :span="6">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">近 14 天激活码激活</div>
					</template>
					<div ref="activationTrendChartRef" class="chart-box"></div>
				</el-card>
			</el-col>
			<el-col :span="12">
				<el-card shadow="hover">
					<template #header>
						<div class="card-title">最新会员订阅</div>
					</template>
					<el-table :data="members" size="small" max-height="300">
						<el-table-column prop="phone" label="手机号" width="130"></el-table-column>
						<el-table-column prop="nickname" label="昵称" width="110"></el-table-column>
						<el-table-column prop="plan_name" label="套餐" width="90">
							<template #default="scope">
								<el-tag size="small" type="primary">{{ scope.row.plan_name }}</el-tag>
							</template>
						</el-table-column>
						<el-table-column prop="status" label="状态" width="90">
							<template #default="scope">
								<el-tag size="small" :type="getStatusTagType(scope.row.status)">
									{{ scope.row.status }}
								</el-tag>
							</template>
						</el-table-column>
						<el-table-column prop="amountText" label="实付" width="80"></el-table-column>
						<el-table-column prop="periodText" label="到期时间" min-width="150"></el-table-column>
					</el-table>
				</el-card>
			</el-col>
		</el-row>
	</div>
</template>

<script setup lang="ts" name="dashboard">
import { ref, onMounted, onUnmounted } from "vue";
import * as echarts from "echarts";
import { getDashboardOverviewApi, getDashboardMembersApi } from "@/api/modules/admin";

type StatCard = {
	key: string;
	icon: string;
	color: string;
	bg: string;
	value: number | string;
	label: string;
};

const statCards = ref<StatCard[]>([
	{ key: "total_users", icon: "User", color: "#409eff", bg: "#ecf5ff", value: 0, label: "总用户数" },
	{ key: "active_today", icon: "Sunny", color: "#67c23a", bg: "#f0f9eb", value: 0, label: "今日活跃" },
	{ key: "active_7d", icon: "AlarmClock", color: "#909399", bg: "#f4f4f5", value: 0, label: "7 日活跃" },
	{ key: "active_subscriptions", icon: "Medal", color: "#e6a23c", bg: "#fdf6ec", value: 0, label: "有效订阅" },
	{ key: "payment_count", icon: "Wallet", color: "#f56c6c", bg: "#fef0f0", value: 0, label: "付费笔数" },
	{ key: "payment_amount", icon: "Money", color: "#9254de", bg: "#f5f0ff", value: "¥0", label: "付费金额" },
	{ key: "activation_used", icon: "Key", color: "#13c2c2", bg: "#e6fffb", value: 0, label: "已用激活码" },
	{ key: "activation_unused", icon: "Unlock", color: "#fa8c16", bg: "#fff7e6", value: 0, label: "未用激活码" }
]);

const registerChartRef = ref<HTMLElement>();
const dauChartRef = ref<HTMLElement>();
const subStatusChartRef = ref<HTMLElement>();
const subPlanChartRef = ref<HTMLElement>();
const paymentChartRef = ref<HTMLElement>();
const activationChartRef = ref<HTMLElement>();
const activationTrendChartRef = ref<HTMLElement>();

const members = ref<any[]>([]);
const charts: echarts.ECharts[] = [];

const fmtYuan = (fen: number): string => {
	const yuan = (fen || 0) / 100;
	return Number.isInteger(yuan) ? String(yuan) : yuan.toFixed(2);
};

const fmtDate = (iso: string | null): string => {
	if (!iso) return "-";
	return iso.slice(0, 10);
};

const getStatusTagType = (status: string): any => {
	const map: Record<string, string> = {
		active: "success",
		pending: "warning",
		unpaid: "warning",
		cancelled: "info",
		expired: "danger",
		past_due: "danger"
	};
	return map[status] || "info";
};

const initChart = (el: HTMLElement | undefined, option: echarts.EChartsOption) => {
	if (!el) return;
	const chart = echarts.init(el);
	chart.setOption(option);
	charts.push(chart);
};

const lineOption = (dates: string[], values: number[], color: string): echarts.EChartsOption => ({
	tooltip: { trigger: "axis" },
	grid: { left: 40, right: 16, top: 24, bottom: 30 },
	xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
	yAxis: { type: "value", minInterval: 1, axisLabel: { fontSize: 10 } },
	series: [
		{
			type: "line",
			data: values,
			smooth: true,
			symbolSize: 6,
			itemStyle: { color },
			areaStyle: { color: `${color}22` },
			lineStyle: { width: 2 }
		}
	]
});

const pieOption = (data: { name: string; value: number }[], colors: string[]): echarts.EChartsOption => ({
	tooltip: { trigger: "item", formatter: "{b}: {c}（{d}%）" },
	legend: { bottom: 0, textStyle: { fontSize: 10 } },
	series: [
		{
			type: "pie",
			radius: ["35%", "62%"],
			center: ["50%", "44%"],
			itemStyle: { borderRadius: 4 },
			label: { show: false },
			data,
			color: colors
		}
	]
});

const barOption = (dates: string[], values: number[], color: string): echarts.EChartsOption => ({
	tooltip: { trigger: "axis" },
	grid: { left: 44, right: 16, top: 24, bottom: 30 },
	xAxis: { type: "category", data: dates, axisLabel: { fontSize: 9 } },
	yAxis: { type: "value", minInterval: 1, axisLabel: { fontSize: 10 } },
	series: [
		{
			type: "bar",
			data: values,
			barMaxWidth: 22,
			itemStyle: { color, borderRadius: [3, 3, 0, 0] }
		}
	]
});

const loadOverview = async () => {
	try {
		const res: any = await getDashboardOverviewApi();
		const d = res.data;

		statCards.value.forEach(card => {
			if (card.key === "payment_amount") {
				card.value = `¥${fmtYuan(d.payment_amount || 0)}`;
			} else if (d[card.key] !== undefined) {
				card.value = d[card.key];
			}
		});

		const regDates = (d.register_trend || []).map((it: any) => it.date.slice(5));
		const regCounts = (d.register_trend || []).map((it: any) => it.count);
		const dauCounts = (d.dau_trend || []).map((it: any) => it.count);
		const actCounts = (d.activation_trend || []).map((it: any) => it.count);

		initChart(registerChartRef.value, lineOption(regDates, regCounts, "#409eff"));
		initChart(dauChartRef.value, lineOption(regDates, dauCounts, "#67c23a"));

		const statusData = (d.sub_by_status || []).map((it: any) => ({
			name: it.status === "active" ? "有效" : it.status,
			value: it.count
		}));
		initChart(
			subStatusChartRef.value,
			pieOption(statusData.length ? statusData : [{ name: "暂无", value: 0 }], ["#67c23a", "#e6a23c", "#909399", "#f56c6c"])
		);

		const planData = (d.sub_by_plan || []).map((it: any) => ({ name: it.name || it.plan, value: it.count }));
		initChart(
			subPlanChartRef.value,
			pieOption(planData.length ? planData : [{ name: "暂无", value: 0 }], ["#409eff", "#f56c6c", "#e6a23c", "#9254de"])
		);

		const payDates = (d.payment_trend || []).map((it: any) => it.date.slice(5));
		const payAmounts = (d.payment_trend || []).map((it: any) => Number((it.amount / 100).toFixed(0)));
		initChart(paymentChartRef.value, barOption(payDates, payAmounts, "#9254de"));

		initChart(
			activationChartRef.value,
			pieOption(
				[
					{ name: "已用", value: d.activation_used || 0 },
					{ name: "未用", value: d.activation_unused || 0 },
					{ name: "已吊销", value: d.activation_revoked || 0 }
				].filter(it => it.value > 0),
				["#13c2c2", "#fa8c16", "#f56c6c"]
			)
		);
		initChart(activationTrendChartRef.value, barOption(regDates, actCounts, "#13c2c2"));
	} catch (e) {
		console.error("运营看板加载失败", e);
	}
};

const loadMembers = async () => {
	try {
		const res: any = await getDashboardMembersApi({ limit: 10 });
		members.value = (res.data.list || []).map((it: any) => ({
			...it,
			amountText: `¥${fmtYuan(it.amount_paid || 0)}`,
			periodText: fmtDate(it.current_period_end)
		}));
	} catch (e) {
		console.error("会员列表加载失败", e);
	}
};

const handleResize = () => {
	charts.forEach(chart => chart.resize());
};

onMounted(() => {
	loadOverview();
	loadMembers();
	window.addEventListener("resize", handleResize);
});

onUnmounted(() => {
	window.removeEventListener("resize", handleResize);
	charts.forEach(chart => chart.dispose());
});
</script>

<style scoped lang="scss">
.dashboard-container {
	padding: 20px;

	.stat-card {
		:deep(.el-card__body) {
			display: flex;
			align-items: center;
			gap: 14px;
			padding: 18px 16px;
		}
	}

	.stat-icon {
		width: 44px;
		height: 44px;
		border-radius: 10px;
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
	}

	.stat-info {
		.stat-value {
			font-size: 22px;
			font-weight: 700;
			color: #303133;
			line-height: 1.2;
		}
		.stat-label {
			font-size: 12px;
			color: #909399;
			margin-top: 2px;
		}
	}

	.card-title {
		font-size: 14px;
		font-weight: 600;
		color: #303133;
	}

	.chart-box {
		width: 100%;
		height: 260px;
	}
}
</style>
