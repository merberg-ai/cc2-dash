const PrinterSettingPhysicalTemplate = /*html*/
    `
        <div class="printer-setting-physical-dialog">
            <div style="flex: 1; overflow: auto;">
                <manual-form-component
                    ref="manualFormComponent"
                    :printer="printer"
                ></manual-form-component>
            </div>
            <div class="add-printer-footer">
                <el-popconfirm :title="$t('printerSetting.confirmDeletePrinter')" :confirm-button-text="$t('confirm')" 
                    :cancel-button-text="$t('cancel')" 
                    @confirm="deletePrinter" 
                    width="200"
                    cancel-button-type="info"
                    placement="top-start">
                    <template #reference>
                        <button class="btn-danger">{{ $t('delete') }}</button>
                    </template>
                </el-popconfirm>
                <button class="btn-primary" @click="confirmPrinter">{{ $t('confirm') }}</button>
            </div>
        </div>
    `;

// Printer Setting Physical Component
const PrinterSettingPhysicalComponent = {
    template: PrinterSettingPhysicalTemplate,
    setup() {
        const printerStore = usePrinterStore();
        return {
            printerStore
        };
    },
    props: {
        printer: {
            type: Object,
            default: null
        }
    },

    emits: ['close-modal'],

    data() {
        return {
        };
    },

    mounted() {

    },

    beforeUnmount() {
    },

    watch: {

    },

    computed: {

    },

    methods: {

        closeModal() {
            this.$refs.manualFormComponent.resetForm();
            this.$emit('close-modal');
        },

        async deletePrinter() {
            try {
                await this.printerStore.requestDeletePrinter(this.printer.printerId);
                this.closeModal();
            } catch (error) {
                // console.error('Failed to delete printer:', error);
            }
        },

        async confirmPrinter() {
            if (this.$refs.manualFormComponent) {
                const updatedPrinter = await this.$refs.manualFormComponent.getFormData();
                if (updatedPrinter) {
                    this.handleUpdatePhysicalPrinter(updatedPrinter);
                }
            }
        },

        async handleUpdatePhysicalPrinter(newPrinter) {
            if (!this.printer) return;

            await this.printerStore.requestUpdatePhysicalPrinter(this.printer.printerId, newPrinter);
            this.closeModal();
        },
    }
};