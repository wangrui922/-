<template>
	<div class="payments-container">
		<div class="table-header">
			<span class="table-title">待确认支付（人工对账）</span>
			<el-button :icon="Refresh" circle @click="loadData"></el-button>
		</div>

		<el-table :data="tableData" border v-loading="loading" empty-text="暂无待确认订单">
			<el-table-column prop="user.phone" label="用户手机号" width="140" />
			<el-table-column prop="user.nickname" label="昵称" width="120" show-overflow-tooltip />
			<el-table-column label="套餐" width="100">
				<template #default="scope">
					<el-tag type="warning">{{ scope.row.plan?.name || "-" }}</el-tag>
				</template>
			</el-table-column>
			<el-table-column label="金额" width="100">
				<template #default="scope">
					<span class="amount">¥{{ scope.row.amount_yuan }}</span>
				</template>
			</el-table-column>
			<el-table-column label="周期" width="80">
				<template #default="scope">
					{{ scope.row.plan?.interval === "year" ? "年付" : scope.row.plan?.interval === "quarter" ? "季付" : "月付" }}
				</template>
			</el-table-column>
			<el-table-column label="支付方式" width="110">
				<template #default="scope">
					<el-tag :type="scope.row.provider === 'wechat' ? 'success' : 'primary'">
						{{ scope.row.provider === "wechat" ? "微信" : "支付宝" }}
					</el-tag>
				</template>
			</el-table-column>
			<el-table-column prop="payer_remark" label="转账备注" width="140" show-overflow-tooltip>
				<template #default="scope">
					<span :class="scope.row.payer_remark ? 'remark' : 'remark-empty'">
						{{ scope.row.payer_remark || "（未填写）" }}
					</span>
				</template>
			</el-table-column>
			<el-table-column prop="created_at" label="下单时间" width="170" />
			<el-table-column label="操作" fixed="right" width="170">
				<template #default="scope">
					<el-button type="success" link @click="handleConfirm(scope.row)">确认收款</el-button>
					<el-button type="danger" link @click="handleReject(scope.row)">拒绝</el-button>
				</template>
			</el-table-column>
		</el-table>

		<el-dialog v-model="rejectVisible" title="拒绝支付" width="420px">
			<el-form label-width="80px">
				<el-form-item label="拒绝原因">
					<el-input v-model="rejectReason" type="textarea" :rows="3" placeholder="例如：未收到对应转账，请核对后重试" />
				</el-form-item>
			</el-form>
			<template #footer>
				<el-button @click="rejectVisible = false">取消</el-button>
				<el-button type="danger" :loading="submitting" @click="doReject">确认拒绝</el-button>
			</template>
		</el-dialog>

		<el-dialog v-model="codeVisible" title="支付已确认 · 激活码已生成" width="520px" :close-on-click-modal="false">
			<div class="code-box">
				<div class="code-label">
					请复制激活码并转发给 {{ codeUser }}，对方在「会员中心」输入后即可开通 {{ codePlan }}（{{ codeDays }} 天）
				</div>
				<div class="code-value" @click="copyCode">{{ confirmedCode }}</div>
				<div class="code-tip">点击激活码即可复制，激活以用户实际激活时刻起算，支持叠加续期</div>
			</div>
			<template #footer>
				<el-button type="primary" @click="copyCode">复制激活码</el-button>
				<el-button @click="codeVisible = false">关闭</el-button>
			</template>
		</el-dialog>
	</div>
</template>

<script setup lang="ts" name="payments">
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { Refresh } from "@element-plus/icons-vue";
import { confirmPaymentApi, getPendingPaymentsApi } from "@/api/modules/billing";

const loading = ref(false);
const submitting = ref(false);
const tableData = ref<any[]>([]);

const rejectVisible = ref(false);
const rejectReason = ref("");
const currentRow = ref<any>(null);

const codeVisible = ref(false);
const confirmedCode = ref("");
const codeUser = ref("");
const codePlan = ref("");
const codeDays = ref(0);

const loadData = async () => {
	loading.value = true;
	try {
		const res: any = await getPendingPaymentsApi();
		tableData.value = res.data || [];
	} catch (e: any) {
		ElMessage.error(e?.msg || "加载失败");
	} finally {
		loading.value = false;
	}
};

const intervalDays: Record<string, number> = { month: 30, quarter: 90, year: 365 };

const copyCode = async () => {
	if (!confirmedCode.value) return;
	try {
		await navigator.clipboard.writeText(confirmedCode.value);
		ElMessage.success("激活码已复制");
	} catch {
		const ta = document.createElement("textarea");
		ta.value = confirmedCode.value;
		document.body.appendChild(ta);
		ta.select();
		document.execCommand("copy");
		document.body.removeChild(ta);
		ElMessage.success("激活码已复制");
	}
};

const handleConfirm = async (row: any) => {
	try {
		const who = row.user?.nickname || row.user?.phone || "该用户";
		const planName = row.plan?.name || "对应";
		const msg = `确认已收到 ${who} 的 ¥${row.amount_yuan} 转账？确认后立即开通 ${planName} 套餐并生成激活码。`;
		await ElMessageBox.confirm(msg, "确认收款", {
			type: "warning",
			confirmButtonText: "确认收款",
			cancelButtonText: "再想想"
		});
	} catch {
		return;
	}
	submitting.value = true;
	try {
		const res: any = await confirmPaymentApi({ subscription_id: row.subscription_id, confirm: true });
		const data = res.data || {};
		if (data.activation_code) {
			confirmedCode.value = data.activation_code;
			codeUser.value = row.user?.nickname || row.user?.phone || "该用户";
			codePlan.value = row.plan?.name || "对应";
			codeDays.value = data.duration_days || intervalDays[data.code_type] || intervalDays[row.plan?.interval] || 30;
			codeVisible.value = true;
		} else {
			ElMessage.success(data.message || "已确认，订阅已激活");
		}
		loadData();
	} catch (e: any) {
		ElMessage.error(e?.msg || "操作失败");
	} finally {
		submitting.value = false;
	}
};

const handleReject = (row: any) => {
	currentRow.value = row;
	rejectReason.value = "";
	rejectVisible.value = true;
};

const doReject = async () => {
	if (!currentRow.value) return;
	submitting.value = true;
	try {
		const res: any = await confirmPaymentApi({
			subscription_id: currentRow.value.subscription_id,
			confirm: false,
			reject_reason: rejectReason.value || "未提供原因"
		});
		ElMessage.success(res.data?.message || "已拒绝该订单");
		rejectVisible.value = false;
		loadData();
	} catch (e: any) {
		ElMessage.error(e?.msg || "操作失败");
	} finally {
		submitting.value = false;
	}
};

onMounted(loadData);
</script>

<style scoped lang="scss">
.payments-container {
	padding: 20px;
}
.table-header {
	display: flex;
	align-items: center;
	justify-content: space-between;
	margin-bottom: 16px;
}
.table-title {
	font-size: 16px;
	font-weight: 700;
}
.amount {
	color: #f56c6c;
	font-weight: 700;
}
.remark {
	color: #303133;
	font-weight: 600;
}
.remark-empty {
	color: #c0c4cc;
}
.code-box {
	text-align: center;
	padding: 4px 0;
}
.code-label {
	font-size: 13px;
	color: #606266;
	margin-bottom: 14px;
	line-height: 1.6;
}
.code-value {
	display: inline-block;
	font-size: 26px;
	font-weight: 800;
	letter-spacing: 3px;
	color: #409eff;
	background: #ecf5ff;
	border: 1px dashed #a0cfff;
	border-radius: 10px;
	padding: 14px 28px;
	cursor: pointer;
	user-select: all;
	font-family: "Consolas", "Menlo", monospace;
}
.code-tip {
	font-size: 12px;
	color: #909399;
	margin-top: 12px;
}
</style>
