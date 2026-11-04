/* Maintenance-only independent JDK cross-check. Not a skill runtime dependency.
 * Uses JDK ZIP/XML, TrueType glyph metrics and PNG/JPEG decoders; does not render OFD.
 * Inputs are explicitly generated validation data, never private/previously denied samples.
 */
import java.awt.Font;
import java.awt.font.FontRenderContext;
import java.awt.font.GlyphVector;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipInputStream;
import javax.imageio.ImageIO;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;

class OfdGenerationCrossCheck {
    static final String NS = "http://www.ofdspec.org/2016";
    static void check(boolean value, String message) {
        if (!value) throw new IllegalArgumentException(message);
    }
    static String child(Element element, String name) {
        NodeList list = element.getElementsByTagNameNS(NS, name);
        check(list.getLength() == 1, "required single child " + name);
        return list.item(0).getTextContent();
    }
    public static void main(String[] args) throws Exception {
        System.setProperty("java.awt.headless", "true");
        if (args.length == 2 && args[0].equals("--make-test-jpeg")) {
            Path path = Path.of(args[1]);
            check(!Files.exists(path), "refuse to replace image input");
            BufferedImage image = new BufferedImage(3, 2, BufferedImage.TYPE_INT_RGB);
            image.setRGB(1, 1, 0x00FF00);
            try (var output = Files.newOutputStream(path, java.nio.file.StandardOpenOption.CREATE_NEW)) {
                check(ImageIO.write(image, "JPEG", output), "JDK JPEG encoder unavailable");
            }
            System.out.println("{\"status\":\"test-jpeg-created\",\"width\":3,\"height\":2}");
            return;
        }
        check(args.length == 1, "expected generated OFD path");
        Map<String, byte[]> members = new HashMap<>();
        try (var zip = new ZipInputStream(Files.newInputStream(Path.of(args[0])))) {
            long total = 0;
            for (var entry = zip.getNextEntry(); entry != null; entry = zip.getNextEntry()) {
                check(members.size() < 64 && !members.containsKey(entry.getName()), "ZIP entries");
                byte[] bytes = zip.readNBytes(8 * 1024 * 1024 + 1);
                total += bytes.length;
                check(bytes.length <= 8 * 1024 * 1024 && total <= 32 * 1024 * 1024, "ZIP byte bound");
                members.put(entry.getName(), bytes);
            }
        }
        var factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(true);
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
        factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
        Map<String, Element> xml = new HashMap<>();
        Set<String> ids = new HashSet<>();
        for (var entry : members.entrySet()) {
            if (!entry.getKey().endsWith(".xml")) continue;
            Element root = factory.newDocumentBuilder().parse(new ByteArrayInputStream(entry.getValue())).getDocumentElement();
            xml.put(entry.getKey(), root);
            NodeList nodes = root.getElementsByTagName("*");
            for (int index = 0; index < nodes.getLength(); index++) {
                Element element = (Element) nodes.item(index);
                if (element.hasAttribute("ID")) check(ids.add(element.getAttribute("ID")), "duplicate ID");
            }
        }
        Element root = xml.get("OFD.xml");
        check(root != null && root.getAttribute("Version").equals("1.0"), "root version");
        String docPath = child(root, "DocRoot");
        Element document = xml.get(docPath);
        check(document != null, "DocRoot target");
        String docDirectory = docPath.substring(0, docPath.lastIndexOf('/') + 1);
        Element resources = xml.get(docDirectory + child(document, "DocumentRes"));
        Map<String, Font> fonts = new HashMap<>();
        NodeList fontNodes = resources.getElementsByTagNameNS(NS, "Font");
        for (int index = 0; index < fontNodes.getLength(); index++) {
            Element font = (Element) fontNodes.item(index);
            String source = docDirectory + resources.getAttribute("BaseLoc") + "/" + child(font, "FontFile");
            Font parsed = Font.createFont(Font.TRUETYPE_FONT, new ByteArrayInputStream(members.get(source))).deriveFont(1000f);
            fonts.put(font.getAttribute("ID"), parsed);
        }
        int decodedImages = 0;
        NodeList imageNodes = resources.getElementsByTagNameNS(NS, "MultiMedia");
        for (int index = 0; index < imageNodes.getLength(); index++) {
            Element image = (Element) imageNodes.item(index);
            String source = docDirectory + resources.getAttribute("BaseLoc") + "/" + child(image, "MediaFile");
            BufferedImage decoded = ImageIO.read(new ByteArrayInputStream(members.get(source)));
            check(decoded != null && decoded.getWidth() > 0 && decoded.getHeight() > 0, "independent image decode");
            decodedImages++;
        }
        int pages = 0, textRuns = 0, characters = 0, checkedAdvances = 0;
        StringBuilder allText = new StringBuilder();
        NodeList pageNodes = document.getElementsByTagNameNS(NS, "Page");
        var context = new FontRenderContext(null, false, true);
        for (int index = 0; index < pageNodes.getLength(); index++) {
            Element pageIndex = (Element) pageNodes.item(index);
            Element page = xml.get(docDirectory + pageIndex.getAttribute("BaseLoc"));
            check(page != null, "Page BaseLoc target");
            pages++;
            NodeList textNodes = page.getElementsByTagNameNS(NS, "TextObject");
            for (int runIndex = 0; runIndex < textNodes.getLength(); runIndex++) {
                Element text = (Element) textNodes.item(runIndex);
                Element code = (Element) text.getElementsByTagNameNS(NS, "TextCode").item(0);
                var matcher = java.util.regex.Pattern.compile("\\\\([0-9A-Fa-f]{4})").matcher(code.getTextContent());
                String decoded = matcher.replaceAll(match -> java.util.regex.Matcher.quoteReplacement(
                    Character.toString((char) Integer.parseInt(match.group(1), 16))));
                Font font = fonts.get(text.getAttribute("Font"));
                check(font != null && font.canDisplayUpTo(decoded) == -1, "independent font coverage");
                GlyphVector glyphs = font.createGlyphVector(context, decoded);
                String[] storedGlyphs = child(text, "Glyphs").split(" ");
                check(storedGlyphs.length == glyphs.getNumGlyphs(), "glyph count");
                String advanceText = code.getAttribute("DeltaX");
                String[] advances = advanceText.isEmpty() ? new String[0] : advanceText.split(" ");
                check(advances.length == Math.max(0, glyphs.getNumGlyphs() - 1), "advance count");
                double size = Double.parseDouble(text.getAttribute("Size"));
                for (int glyph = 0; glyph < glyphs.getNumGlyphs(); glyph++) {
                    check(glyphs.getGlyphCode(glyph) == Integer.parseInt(storedGlyphs[glyph]), "independent glyph ID");
                    if (glyph < advances.length) {
                        double actual = glyphs.getGlyphMetrics(glyph).getAdvanceX() * size / 1000.0;
                        check(Math.abs(actual - Double.parseDouble(advances[glyph])) < 0.0002, "independent advance width");
                        checkedAdvances++;
                    }
                }
                textRuns++;
                characters += decoded.length();
                allText.append(decoded);
            }
        }
        check(allText.indexOf("中文生成验证") >= 0, "frozen independent Chinese expected literal");
        int maximum = Integer.parseInt(child(document, "MaxUnitID"));
        check(maximum == ids.stream().mapToInt(Integer::parseInt).max().orElse(-1), "MaxUnitID");
        System.out.printf("{\"status\":\"passed\",\"tool\":\"JDK ZIP/XML/Font/ImageIO\","
            + "\"pages\":%d,\"text_runs\":%d,\"characters\":%d,\"checked_advances\":%d,"
            + "\"decoded_images\":%d,\"visual_ofd_rendering\":\"not_performed\"}%n",
            pages, textRuns, characters, checkedAdvances, decodedImages);
    }
}
