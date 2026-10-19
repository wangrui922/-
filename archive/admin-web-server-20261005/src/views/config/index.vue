<template>
	<div class="config-container">
		<el-card shadow="hover">
			<template #header>
				<div class="card-header">
					<span>AI 引导阈值配置</span>
					<el-button type="danger" size="small" @click="handleReset">重置为默认值</el-button>
				</div>
			</template>
			<el-table :data="configs" size="small" v-loading="loading" style="width: 100%">
				<el-table-column prop="key" label="配置项" width="200">
					<template #default="scope">
						<el-tag size="small" type="info">{{ scope.row.key }}</el-tag>
					</template>
				</el-table-column>
				<el-table-column prop="description" label="说明" min-width="300" />
				<el-table-column prop="default_value" label="默认值" width="100">
					<template #default="scope">
						<el-tag size="small" type="success">{{ scope.row.default_value }}</el-tag>
					</template>
				</el-table-column>
				<el-table-column prop="value" label="当前值" width="200">
					<template #default="scope">
						<el-input v-model="scope.row.value" size="small" :placeholder="scope.row.default_value" style="width: 120px" />
					</template>
				</el-table-column>
				<el-table-column label="操作" width="120" fixed="right">
					<template #default="scope">
						<el-button type="primary" size="small" @click="handleSave(scope.row)">保存</el-button>
					</template>
				</el-table-column>
			</el-table>
		</el-card>
	</div>
</template>

<script setup lang="ts" name="config">
import { ref, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getSystemConfigsApi, updateSystemConfigApi, resetSystemConfigsApi } from "@/api/modules/config";

interface ConfigItem {
	key: string;
	value: string;
	default_value: string;
	description: string;
}

const configs = ref<ConfigItem[]>([]);
const loading = ref(false);

const loadConfigs = async () => {
	loading.value = true;
	try {
		const res: any = await getSystemConfigsApi();
		configs.value = res.data.items || [];
	} catch (e) {
		ElMessage.error("加载配置失败");
	} finally {
		loading.value = false;
	}
};

const handleSave = async (row: ConfigItem) => {
	try {
		await updateSystemConfigApi(row.key, row.value);
		ElMessage.success(`${row.key} 已更新为 ${row.value}`);
	} catch (e) {
		ElMessage.error("保存失败");
	}
};

const handleReset = async () => {
	try {
		await ElMessageBox.confirm("确定将所有配置重置为默认值？", "提示", {
			confirmButtonText: "确定",
			cancelButtonText: "取消",
			type: "warning"
		});
		await resetSystemConfigsApi();
		ElMessage.success("已重置为默认值");
		loadConfigs();
	} catch (e: any) {
		if (e !== "cancel") {
			ElMessage.error("重置失败");
		}
	}
};

onMounted(() => {
	loadConfigs();
});
</script>

<style scoped lang="scss">
.config-container {
	padding: 20px;

	.card-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: 16px;
		font-weight: 600;
	}
}
</style>
