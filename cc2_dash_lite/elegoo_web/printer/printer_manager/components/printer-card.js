const PrinterCardTemplate = /*html*/`
	<div class="fdm-card">
		<div class="fdm-card-header">
			<slot name="title"></slot>
			<!-- 切换图标，当 isOpen 的值为 true 时，rotated 类会被添加到元素上 -->
			<span class="toggle-icon" :class="{ rotated: localOpen }" @click="toggle">
                <svg fill="none" version="1.1" width="20" height="20" viewBox="0 0 20 20">
                        <defs>
                            <clipPath id="master_svg0_152_05448">
                                <rect x="20" y="0" width="20" height="20" rx="0" />
                            </clipPath>
                        </defs>
                        <g transform="matrix(0,1,-1,0,20,-20)"
                            clip-path="url(#master_svg0_152_05448)">
                            <g>
                                <path
                                    d="M27.3867681575,5.53022403Q27.2813000675,5.42475593,27.2242211075,5.28695518Q27.1671421575,5.14915444,27.1671421575,5Q27.1671421575,4.926146217,27.1815503875,4.85371152Q27.1959584975,4.78127682,27.2242211075,4.71304482Q27.2524837275,4.64481282,27.2935147275,4.58340567Q27.3345456675,4.52199849,27.3867681575,4.46977597Q27.4389906775,4.4175534800000005,27.5003978575,4.37652254Q27.5618050075,4.33549154,27.6300370075,4.30722892Q27.6982690075,4.27896631,27.7707037075,4.2645582Q27.8431384045,4.25014997,27.9169921875,4.25014997Q28.0661466275,4.25014997,28.2039473675,4.30722886Q28.3417481175,4.36430788,28.4472162175,4.46977597L28.4473222475,4.46966994L33.4473218875,9.4696703Q33.5528106875,9.5751591,33.6099013875,9.7129874Q33.6669911875,9.8508158,33.6669911875,10Q33.6669911875,10.1491842,33.6099009875,10.2870121Q33.5528106875,10.4248405,33.4473218875,10.5303297L28.4473222475,15.53033L28.4472165075,15.530224Q28.3417483575,15.635692,28.2039475475,15.69277Q28.0661467175,15.749849,27.9169921875,15.74985Q27.8431384045,15.749849,27.7707037075,15.735441Q27.6982690075,15.721033,27.6300370075,15.69277Q27.5618050075,15.664507,27.5003978575,15.623476Q27.4389906775,15.582445,27.3867681575,15.530224Q27.3345456675,15.478001,27.2935147275,15.416594Q27.2524837275,15.355186,27.2242211075,15.286955Q27.1959584975,15.218722,27.1815503875,15.146288Q27.1671421575,15.073853,27.1671421575,15Q27.1671421575,14.8508453,27.2242209875,14.7130442Q27.2812998875,14.575243,27.3867678675,14.4697762L27.3866621275,14.4696703L31.8563327875,10L27.3866621275,5.53033006L27.3867681575,5.53022403Z"
                                    fill-rule="evenodd" fill="currentColor"
                                    fill-opacity="0.800000011920929" />
                            </g>
                        </g>
                    </svg>
			</span>
		</div>
		<!-- ElementPlus 的折叠过渡动画组件 -->
		<el-collapse-transition>
			<div v-show="localOpen" ref="content" class="fdm-card-content">
				<slot></slot>
			</div>
		</el-collapse-transition>
	</div>
`;

const PrinterCardComponent = {
    template: PrinterCardTemplate,
    components: {

    },
    props: {
        isOpen: true,
    },
    data() {
        return {
            localOpen: this.isOpen,
        };
    },
    watch: {
        isOpen(val) {
            this.localOpen = val;
            console.log("isOpen changed:", val);
        },
        localOpen(val) {
            this.$emit('update:isOpen', val);
        },
    },
    methods: {
        toggle() {
            this.localOpen = !this.localOpen;
        },
    },
};

// 样式注入
if (typeof window !== 'undefined' && !document.getElementById('printer-card-style')) {
    const style = document.createElement('style');
    style.id = 'printer-card-style';
    style.innerHTML = `
	.fdm-card {
		border: none;
		border-radius: 8px;
		overflow: hidden;
	}
	.fdm-card-header {
		border-radius: 8px 8px 0px 0px;
		background: var(--bg-quaternary);
		padding: 12px 18px;
		display: flex;
		justify-content: space-between;
		align-items: center;
		min-height: 30px;
	}
	.fdm-card-content {
		overflow: hidden;
		background: hsla(var(--ef-card-background-color));
	}
	.toggle-icon {
		display: flex;
		width: 26px;
		height: 26px;
		justify-content: center;
		align-items: center;
		transition: transform 0.3s ease;
		margin-right: 0px;
		cursor: pointer;
	}
	.toggle-icon.rotated {
		transform: rotate(180deg);
	}
	`;
    document.head.appendChild(style);
}
