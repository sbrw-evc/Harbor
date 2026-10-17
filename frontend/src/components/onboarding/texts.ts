import { k } from "@/i18n";

/** The texts of a step: its name, what to do while it is not done and the label of its button. */
export interface StepText {
  title: string;
  text: string;
  go?: string;
}

/** The "First steps" checklist of the overview page, by step ID (services/onboarding.py). */
export const CHECKLIST: Record<string, StepText> = {
  spa: {
    title: k("Set up the sign-in"),
    text: k("Users of the panel need an administrator; with Keystone, the connection must work and somebody must have the Administrator role of Harbor there. Keystone can be connected later, with the local users moved there."),
    go: k("Open the sign-in settings"),
  },
  router: {
    title: k("Connect the router"),
    text: k("Enter the address of the MikroTik, add the SSH key of the panel to it and check the connection: the router must answer polls."),
    go: k("Open the infrastructure"),
  },
  vps: {
    title: k("Connect the Amnezia server"),
    text: k("Enter the address of the VPS with the AmneziaWG container and add the SSH key of the panel to it: VPN clients and selective routing work through it."),
    go: k("Open the infrastructure"),
  },
  gateway: {
    title: k("Start the gateway"),
    text: k("Enter the address of this host and create the gateway client: the panel writes its config and the tunnel starts by itself."),
    go: k("Open the infrastructure"),
  },
  address: {
    title: k("Set the application address"),
    text: k("The address of the panel (Settings → Panel) must be the address people open, not localhost: Keystone sends people back to it and config links use it."),
    go: k("Check the address"),
  },
  device: {
    title: k("Add the first device"),
    text: k("Add a router, switch or controller and check that it answers a poll: the panel reads its interfaces, neighbours and clients."),
    go: k("Open devices"),
  },
  flows: {
    title: k("Receive flows (NetFlow / IPFIX)"),
    text: k("Turn on flow export on a device (Flow export in its actions) or point the router at the collector: flow analysis, threats and detection rules work on these flows."),
    go: k("Set up flow export"),
  },
  vpn_client: {
    title: k("Create a VPN client"),
    text: k("Create the first AmneziaWG or MikroTik client and hand its config over with a QR code or a one-time link."),
    go: k("Open VPN clients"),
  },
  telegram: {
    title: k("Turn on Telegram notifications"),
    text: k("Connect a bot made with @BotFather, enter the chat id and turn notifications on, so that alerts reach you."),
    go: k("Set up notifications"),
  },
  backups: {
    title: k("Keep backups"),
    text: k("Leave the daily backup on and make the first one: it holds the database and the OpenBao snapshot."),
    go: k("Open backups"),
  },
};

/** The setup guides of the pages, by page and step ID (services/onboarding.py, GUIDES). */
export const GUIDES: Record<string, { intro: string; steps: Record<string, StepText> }> = {
  devices: {
    intro: k("Add your network devices, check that they answer and see who is on the network."),
    steps: {
      add: { title: k("Add a device"), text: k("Add a router, switch, firewall, access point or controller with its address and credentials (kept in OpenBao)."), go: k("Add a device") },
      answers: { title: k("Check that it answers"), text: k("The status turns Up after a successful poll. If it does not, open the device, check the address, port and credentials and press Poll now.") },
      endpoints: { title: k("See the clients of the network"), text: k("Phones, laptops and TVs appear from ARP, DHCP, Wi-Fi and switch tables after a poll."), go: k("Open network clients") },
      discover: { title: k("Find other devices"), text: k("Scan a subnet over SNMP to add switches and access points in one go."), go: k("Find on the network") },
      map: { title: k("Arrange the network map"), text: k("Drag the devices on the network map: the positions are kept for everyone."), go: k("Open the map") },
    },
  },
  flows: {
    intro: k("Devices send NetFlow / IPFIX to the collector, and the panel shows who talks to whom."),
    steps: {
      device: { title: k("Add the exporting device"), text: k("Flows are matched to a device by its address: add the router or switch that will export them."), go: k("Open devices") },
      export: { title: k("Turn on flow export"), text: k("Open the device and run Flow export in its actions, or set NetFlow v9 / IPFIX on it by hand with the collector address and port from the settings. The first flows appear here within a minute."), go: k("Open devices") },
      rules: { title: k("Check the detection rules"), text: k("Rules turn DDoS attacks, scans and contacts with malicious addresses into alerts: turn on the ones you need."), go: k("Open rules") },
      threats: { title: k("Load threat lists"), text: k("Public blocklists mark flows with malicious addresses; they refresh on their own every few hours."), go: k("Open threats") },
      view: { title: k("Save a view"), text: k("Build a chart in Visualization and save it as a view to open it in one click."), go: k("Open Visualization") },
    },
  },
  clients: {
    intro: k("Give people VPN access: create a client, hand over its config and see it connect."),
    steps: {
      client: { title: k("Create a client"), text: k("Choose the AmneziaWG server or MikroTik and a name; a quota and an end date are optional."), go: k("New client") },
      config: { title: k("Hand over the config"), text: k("Open the client and download its config, show the QR code or send a one-time link.") },
      connected: { title: k("Wait for the first connection"), text: k("Once the person imports the config, the client goes online and its traffic and location appear here.") },
      policy: { title: k("Set default policies"), text: k("A default quota and access period apply to every new client."), go: k("Open settings") },
      telegram: { title: k("Get notified"), text: k("Telegram notifications tell you when a quota runs out or access is about to end."), go: k("Set up notifications") },
    },
  },
  routing: {
    intro: k("Send chosen sites through the VPN: the router keeps the vpn-sites list, other traffic goes through the provider."),
    steps: {
      router: { title: k("Connect the router"), text: k("The MikroTik from ROUTER_HOST is added on its own; it has to answer over SSH with the key of the panel."), go: k("Open devices") },
      site_list: { title: k("Upload the site list"), text: k("Upload ip-list.json: the panel compiles the subnets and updates the vpn-sites address list on the router."), go: k("Upload ip-list.json") },
      custom: { title: k("Add your own addresses"), text: k("Add a domain or a subnet under Own addresses through the VPN below to send it through the VPN too.") },
      flows: { title: k("See what goes through the VPN"), text: k("With flow intake set up, flow analysis shows the traffic of each site and route."), go: k("Open flow analysis") },
    },
  },
  settings: {
    intro: k("Finish the setup of the panel: sign-in, its address, notifications and backups."),
    steps: {
      spa: { title: k("Connect to SPA"), text: k("Give yourself the Administrator role in SPA before connecting: the first synchronization disables the old local admin. Then check that the synchronization below runs without errors."), go: k("Synchronize now") },
      address: { title: k("Set the application address"), text: k("The address of the panel (the Panel card of this page) must be the address people open, not localhost; it applies at once.") },
      telegram: { title: k("Turn on Telegram notifications"), text: k("Paste the bot token under Telegram bot, enter the chat id under Notifications, turn on Send notifications and save."), go: k("Show notifications") },
      backups: { title: k("Keep backups"), text: k("Leave Daily backup on and make the first backup."), go: k("Open backups") },
      policy: { title: k("Set default policies"), text: k("A default quota and access period for new VPN clients, under Default policies.") },
    },
  },
};
