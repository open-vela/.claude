#!/bin/bash
# pre-commit hook: 检查头文件变更时 API 文档是否同步更新
# 安装: cp scripts/hooks/check-api-doc-sync.sh .git/hooks/pre-commit

# 头文件 -> API 文档的映射关系
declare -A HEADER_DOC_MAP=(
    # ========== 内核 ==========
    ["nuttx/include/pthread.h"]="docs/zh-cn/api/kernel/thread.md"
    ["nuttx/include/sched.h"]="docs/zh-cn/api/kernel/sched.md"
    ["nuttx/include/signal.h"]="docs/zh-cn/api/kernel/signal.md"
    ["nuttx/include/mqueue.h"]="docs/zh-cn/api/kernel/msgqueue.md"
    ["nuttx/include/nuttx/mm/mm.h"]="docs/zh-cn/api/kernel/mem.md"

    # ========== 网络 ==========
    ["nuttx/include/sys/socket.h"]="docs/zh-cn/api/network/net.md"
    ["nuttx/include/netdb.h"]="docs/zh-cn/api/network/net.md"
    ["nuttx/include/nuttx/net/dns.h"]="docs/zh-cn/api/network/net.md"

    # ========== 蓝牙 ==========
    ["frameworks/connectivity/bluetooth/framework/include/bt_adapter.h"]="docs/zh-cn/api/framework/bluetooth/bt_gap.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_device.h"]="docs/zh-cn/api/framework/bluetooth/bt_device.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_a2dp.h"]="docs/zh-cn/api/framework/bluetooth/bt_a2dp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_a2dp_sink.h"]="docs/zh-cn/api/framework/bluetooth/bt_a2dp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_a2dp_source.h"]="docs/zh-cn/api/framework/bluetooth/bt_a2dp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_gattc.h"]="docs/zh-cn/api/framework/bluetooth/bt_gatt.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_gatts.h"]="docs/zh-cn/api/framework/bluetooth/bt_gatt.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_hfp_hf.h"]="docs/zh-cn/api/framework/bluetooth/bt_hfp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_hfp_ag.h"]="docs/zh-cn/api/framework/bluetooth/bt_hfp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_hfp.h"]="docs/zh-cn/api/framework/bluetooth/bt_hfp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_avrcp.h"]="docs/zh-cn/api/framework/bluetooth/bt_avrcp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_avrcp_control.h"]="docs/zh-cn/api/framework/bluetooth/bt_avrcp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_avrcp_target.h"]="docs/zh-cn/api/framework/bluetooth/bt_avrcp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_hid_device.h"]="docs/zh-cn/api/framework/bluetooth/bt_hid.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_pan.h"]="docs/zh-cn/api/framework/bluetooth/bt_pan.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_spp.h"]="docs/zh-cn/api/framework/bluetooth/bt_spp.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_le_scan.h"]="docs/zh-cn/api/framework/bluetooth/bt_le_scan.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_le_advertiser.h"]="docs/zh-cn/api/framework/bluetooth/bt_le_advertiser.md"
    ["frameworks/connectivity/bluetooth/framework/include/bt_cs.h"]="docs/zh-cn/api/framework/bluetooth/bt_cs.md"

    # ========== 多媒体 ==========
    ["frameworks/multimedia/media/include/media_player.h"]="docs/zh-cn/api/framework/media/media_player.md"
    ["frameworks/multimedia/media/include/media_recorder.h"]="docs/zh-cn/api/framework/media/media_recorder.md"
    ["frameworks/multimedia/media/include/media_focus.h"]="docs/zh-cn/api/framework/media/media_focus.md"
    ["frameworks/multimedia/media/include/media_policy.h"]="docs/zh-cn/api/framework/media/media_policy.md"
    ["frameworks/multimedia/media/include/media_session.h"]="docs/zh-cn/api/framework/media/media_session.md"
    ["frameworks/multimedia/media/include/media_trigger.h"]="docs/zh-cn/api/framework/media/media_trigger.md"
    ["frameworks/multimedia/media/include/media_trigger_model.h"]="docs/zh-cn/api/framework/media/media_trigger_model.md"
    ["frameworks/multimedia/media/include/media_utils.h"]="docs/zh-cn/api/framework/media/media_utils.md"

    # ========== Telephony ==========
    ["frameworks/connectivity/telephony/include/tapi_manager.h"]="docs/zh-cn/api/framework/telephony/telephony_manager.md"
    ["frameworks/connectivity/telephony/include/tapi_call.h"]="docs/zh-cn/api/framework/telephony/telephony_call.md"
    ["frameworks/connectivity/telephony/include/tapi_sms.h"]="docs/zh-cn/api/framework/telephony/telephony_sms.md"
    ["frameworks/connectivity/telephony/include/tapi_data.h"]="docs/zh-cn/api/framework/telephony/telephony_data.md"
    ["frameworks/connectivity/telephony/include/tapi_network.h"]="docs/zh-cn/api/framework/telephony/telephony_network.md"
    ["frameworks/connectivity/telephony/include/tapi_sim.h"]="docs/zh-cn/api/framework/telephony/telephony_sim.md"
    ["frameworks/connectivity/telephony/include/tapi_ims.h"]="docs/zh-cn/api/framework/telephony/telephony_ims.md"
    ["frameworks/connectivity/telephony/include/tapi_ss.h"]="docs/zh-cn/api/framework/telephony/telephony_ss.md"
    ["frameworks/connectivity/telephony/include/tapi_stk.h"]="docs/zh-cn/api/framework/telephony/telephony_stk.md"
    ["frameworks/connectivity/telephony/include/tapi_phonebook.h"]="docs/zh-cn/api/framework/telephony/telephony_phonebook.md"
    ["frameworks/connectivity/telephony/include/tapi_phone.h"]="docs/zh-cn/api/framework/telephony/telephony_phone.md"
    ["frameworks/connectivity/telephony/include/tapi_cbs.h"]="docs/zh-cn/api/framework/telephony/telephony_cbs.md"

    # ========== 系统框架 ==========
    ["apps/system/uorb/uORB/uORB.h"]="docs/zh-cn/api/framework/uorb.md"
    ["frameworks/system/utils/include/kvdb.h"]="docs/zh-cn/api/framework/kvdb.md"
)

warnings=0
changed_files=$(git diff --cached --name-only)

for header in "${!HEADER_DOC_MAP[@]}"; do
    doc="${HEADER_DOC_MAP[$header]}"
    # 检查头文件是否在本次提交中变更
    if echo "$changed_files" | grep -q "$header"; then
        # 检查对应文档是否也在本次提交中变更
        if ! echo "$changed_files" | grep -q "$doc"; then
            echo "⚠️  WARNING: $header 已变更，但 API 文档 $doc 未同步更新"
            warnings=$((warnings + 1))
        fi
    fi
done

if [ "$warnings" -gt 0 ]; then
    echo ""
    echo "检测到 $warnings 个头文件变更未同步 API 文档。"
    echo "请更新对应的 API 文档后再提交。"
    echo "如确认无需更新文档，使用 git commit --no-verify 跳过检查。"
    exit 1
fi

exit 0
