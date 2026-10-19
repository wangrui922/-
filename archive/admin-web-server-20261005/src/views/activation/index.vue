<template>
	<div class="activation-container">
		<div class="table-header">
			<el-button type="primary" :icon="CirclePlus" @click="openCreateDialog">生成激活码</el-button>
			<el-button :icon="Refresh" circle @click="loadData"></el-button>
		</div>
		<el-table :data="tableData" border v-loading="loading">
			<el-table-column prop="id" label="ID" width="240" show-overflow-tooltip />
			<el-table-column prop="label" label="备注" show-overflow-tooltip />
			<el-table-column prop="used" label="状态" width="100">
				<template #default="scope">
					<el-tag :type="scope.row.used ? 'success' : 'info'">{{ scope.row.used ? "已使用" : "未使用" }}</el-tag>
				</template>
			</el-table-column>
			<el-table-column prop="user_id" label="绑定用户" width="240" show-overflow-tooltip />
			<el-table-column prop="activated_at" label="激活时间" width="180" />
			<el-table-column prop="expires_at" label="过期时间" width="180" />
			<el-table-column prop="created_at" label="创建时间" width="180" />
		</el-table>
		<el-pagination
			v-model:current-page="page"
			v-model:page-size="size"
			:total="total"
			:page-sizes="[10, 20, 50]"
			layout="total, sizes, prev, pager, next"
			@size-change="loadData"
			@current-change="loadData"
			style="margin-top: 16px"
		/>

		<el-dialog v-model="dialogVisible" title="生成激活码" width="400px">
			<el-form :model="form" label-width="80px">
				<el-form-item label="数量">
					<el-input-number v-model="form.count" :min="1" :max="500" />
				</el-form-item>
				<el-form-item label="备注">
					<el-input v-model="form.label" placeholder="选填" />
				</el-form-item>
			</el-form>
			<template #footer>
				<el-button @click="dialogVisible = false">取消</el-button>
				<el-button type="primary" @click="handleCreate">确定</el-button>
			</template>
		</el-dialog>
	</div>
</template>

<script setup lang="ts" name="activation">
import { ref, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { CirclePlus, Refresh } from "@element-plus/icons-vue";
import { getActivationCodesApi, createActivationCodesApi } from "@/api/modules/admin";

const loading = ref(false);
const tableData = ref([]);
const page = ref(1);
const size = ref(20);
const total = ref(0);
const dialogVisible = ref(false);
const form = ref({ count: 10, label: "" });

const loadData = async () => {
	loading.value = true;
	try {
		const res: any = await getActivationCodesApi({ page: page.value, size: size.value });
		const d = res.data;
		tableData.value = d.items || [];
		total.value = d.total || 0;
	} catch (e) {
		ElMessage.error("加载失败");
	} finally {
		loading.value = false;
	}
};

const openCreateDialog = () => {
	form.value = { count: 10, label: "" };
	dialogVisible.value = true;
};

const handleCreate = async () => {
	try {
		const res: any = await createActivationCodesApi({ count: form.value.count, label: form.value.label });
		const codes = res.data?.codes || [];
		ElMessage.success(`成功生成 ${codes.length} 个激活码`);
		dialogVisible.value = false;
		loadData();
	} catch (e: any) {
		ElMessage.error(e.msg || "生成失败");
	}
};

onMounted(loadData);
</script>

<style scoped lang="scss">
.activation-container {
	padding: 20px;
}
.table-header {
	margin-bottom: 16px;
	display: flex;
	gap: 12px;
}
</style>
